"""OpenAI provider adapter and governed model discovery."""
from __future__ import annotations
import json, os, socket, time, urllib.error, urllib.request
from typing import Any, Sequence
from ..audit import now_utc
from ..discovery import DiscoveredModelCandidate
from ..errors import ProviderConfigurationError, ProviderErrorContext, ProviderExecutionError, ProviderHTTPError, ProviderResponseError, ProviderTimeoutError, ProviderTransportError
from ..multimodal import MultimodalContentError, openai_messages
from ..models import InferenceRequest, InferenceResponse, InferenceTelemetry, InferenceUsage, ModelSpec, new_execution_id

class OpenAIAPIProvider:
    provider_id="openai"
    def __init__(self,spec:ModelSpec,api_key:str|None=None,timeout:float=60.0):
        self.spec=spec; self.api_key=api_key or os.getenv("OPENAI_API_KEY"); self.timeout=timeout
    def health(self)->bool:
        return bool(self.spec.endpoint and self.api_key)
    def generate(self,request:InferenceRequest,*,selected_model:str|None=None,execution_id:str="")->InferenceResponse:
        selected=selected_model or self.spec.id; execution_id=execution_id or new_execution_id(); started_at=now_utc(); started=time.monotonic()
        context=ProviderErrorContext(self.provider_id,request.request_id,execution_id,request.model,selected,self.spec.id)
        if not self.health(): raise ProviderConfigurationError("OpenAI provider is not configured",context=context)
        try: messages=openai_messages(request.messages,allowed_modalities=frozenset({"text","image"}))
        except MultimodalContentError as exc: raise ProviderResponseError(f"OpenAI request contains unsupported multimodal content: {exc}",context=context) from exc
        payload:dict[str,Any]={"model":request.model or self.spec.id,"input":messages}
        if request.temperature is not None: payload["temperature"]=request.temperature
        if request.max_tokens is not None: payload["max_output_tokens"]=request.max_tokens
        req=urllib.request.Request(self.spec.endpoint.rstrip("/")+"/responses",data=json.dumps(payload).encode(),method="POST",
            headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json","Accept":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response: raw=json.loads(response.read().decode())
        except urllib.error.HTTPError as exc: raise ProviderHTTPError(f"OpenAI HTTP request failed with status {exc.code}",context=context,status_code=exc.code) from None
        except (TimeoutError,socket.timeout): raise ProviderTimeoutError("OpenAI inference request timed out",context=context) from None
        except urllib.error.URLError: raise ProviderTransportError("OpenAI inference transport failed",context=context) from None
        except (UnicodeDecodeError,json.JSONDecodeError): raise ProviderResponseError("OpenAI inference response was not valid JSON",context=context) from None
        content=_extract_content(raw,context); usage=_extract_usage(raw,context); reported=_extract_reported_model(raw,context)
        telemetry=InferenceTelemetry(request.request_id,execution_id,self.provider_id,self.spec.id,started_at,int((time.monotonic()-started)*1000),True)
        return InferenceResponse(model=self.spec.id,provider=self.provider_id,content=content,raw=raw,request_id=request.request_id,execution_id=execution_id,
            requested_model=request.model,selected_model=selected,provider_model=self.spec.id,reported_model=reported,usage=usage,telemetry=telemetry)

class OpenAIModelDiscoveryProvider:
    provider_id="openai"
    def __init__(self,api_key:str|None=None,base_url:str|None=None,timeout:float=30.0):
        self.api_key=api_key or os.getenv("OPENAI_API_KEY"); self.base_url=(base_url or os.getenv("OPENAI_API_ENDPOINT","https://api.openai.com/v1")).rstrip("/"); self.timeout=timeout
    def discover(self)->Sequence[DiscoveredModelCandidate]:
        if not self.api_key: raise ProviderConfigurationError("OpenAI model discovery requires OPENAI_API_KEY",context=ProviderErrorContext(self.provider_id,"discovery","discovery"))
        req=urllib.request.Request(self.base_url+"/models",headers={"Authorization":f"Bearer {self.api_key}","Accept":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response: raw=json.loads(response.read().decode())
        except urllib.error.HTTPError as exc: raise RuntimeError(f"OpenAI model discovery returned HTTP {exc.code}") from None
        except (TimeoutError,socket.timeout): raise RuntimeError("OpenAI model discovery timed out") from None
        except urllib.error.URLError: raise RuntimeError("OpenAI model discovery transport failed") from None
        except (UnicodeDecodeError,json.JSONDecodeError): raise RuntimeError("OpenAI model discovery returned invalid JSON") from None
        data=raw.get("data") if isinstance(raw,dict) else None
        if not isinstance(data,list): raise RuntimeError("OpenAI model discovery response missing data list")
        seen=set(); out=[]
        for item in data:
            if not isinstance(item,dict): continue
            model_id=str(item.get("id") or "").strip()
            if not model_id or model_id in seen: continue
            seen.add(model_id)
            out.append(DiscoveredModelCandidate(
                candidate_id=f"openai:{model_id}",provider_id=self.provider_id,
                model=ModelSpec(model_id,self.provider_id,self.base_url,("chat","reasoning"),None,True),
                metadata={"owned_by":item.get("owned_by"),"created":item.get("created")},
                discovered_at=now_utc()))
        return tuple(out)

def _extract_content(raw:object,context:ProviderErrorContext)->str:
    if not isinstance(raw,dict): raise ProviderResponseError("OpenAI response body must be a JSON object",context=context)
    text=raw.get("output_text")
    if isinstance(text,str) and text: return text
    output=raw.get("output")
    if isinstance(output,list):
        parts=[]
        for item in output:
            if not isinstance(item,dict): continue
            for content in item.get("content",[]) if isinstance(item.get("content"),list) else []:
                if isinstance(content,dict) and isinstance(content.get("text"),str): parts.append(content["text"])
        if parts: return "\n".join(parts)
    raise ProviderResponseError("OpenAI response did not contain output text",context=context)

def _extract_usage(raw:object,context:ProviderErrorContext)->InferenceUsage:
    usage=raw.get("usage") if isinstance(raw,dict) else None
    if usage is None: return InferenceUsage()
    if not isinstance(usage,dict): raise ProviderResponseError("OpenAI response usage must be an object",context=context)
    return InferenceUsage(_as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens")), _as_int(usage.get("total_tokens")))

def _extract_reported_model(raw:object,context:ProviderErrorContext)->str|None:
    value=raw.get("model") if isinstance(raw,dict) else None
    return value.strip() if isinstance(value,str) and value.strip() else None

def _as_int(value:object)->int|None:
    if isinstance(value,bool): return None
    if isinstance(value,int): return value
    if isinstance(value,float) and value.is_integer(): return int(value)
    return None
