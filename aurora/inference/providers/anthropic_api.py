"""Anthropic Claude provider adapter and governed model discovery."""
from __future__ import annotations
import json, os, socket, time, urllib.error, urllib.request
from typing import Any, Sequence
from ..audit import now_utc
from ..discovery import DiscoveredModelCandidate
from ..errors import ProviderConfigurationError, ProviderErrorContext, ProviderHTTPError, ProviderResponseError, ProviderTimeoutError, ProviderTransportError
from ..models import InferenceRequest, InferenceResponse, InferenceTelemetry, InferenceUsage, ModelSpec, new_execution_id

class AnthropicAPIProvider:
    provider_id="anthropic"
    def __init__(self,spec:ModelSpec,api_key:str|None=None,timeout:float=60.0):
        self.spec=spec; self.api_key=api_key or os.getenv("ANTHROPIC_API_KEY"); self.timeout=timeout
    def health(self)->bool:
        return bool(self.spec.endpoint and self.api_key)
    def generate(self,request:InferenceRequest,*,selected_model:str|None=None,execution_id:str="")->InferenceResponse:
        selected=selected_model or self.spec.id; execution_id=execution_id or new_execution_id(); started_at=now_utc(); started=time.monotonic()
        context=ProviderErrorContext(self.provider_id,request.request_id,execution_id,request.model,selected,self.spec.id)
        if not self.health(): raise ProviderConfigurationError("Anthropic provider is not configured",context=context)
        system_parts=[]; messages=[]
        for message in request.messages:
            role=str(message.get("role","user"))
            content=message.get("content","")
            if isinstance(content,list):
                text="\n".join(str(item.get("text","")) for item in content if isinstance(item,dict) and item.get("type")=="text")
            else: text=str(content)
            if role=="system": system_parts.append(text)
            else: messages.append({"role":"assistant" if role=="assistant" else "user","content":text})
        if not messages: raise ProviderResponseError("Anthropic request contains no user/assistant messages",context=context)
        payload:dict[str,Any]={"model":request.model or self.spec.id,"messages":messages,"max_tokens":request.max_tokens or 3000}
        if system_parts: payload["system"]="\n\n".join(system_parts)
        if request.temperature is not None: payload["temperature"]=request.temperature
        req=urllib.request.Request(self.spec.endpoint.rstrip("/")+"/messages",data=json.dumps(payload).encode(),method="POST",
            headers={"x-api-key":str(self.api_key),"anthropic-version":"2023-06-01","content-type":"application/json","accept":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response: raw=json.loads(response.read().decode())
        except urllib.error.HTTPError as exc: raise ProviderHTTPError(f"Anthropic HTTP request failed with status {exc.code}",context=context,status_code=exc.code) from None
        except (TimeoutError,socket.timeout): raise ProviderTimeoutError("Anthropic inference request timed out",context=context) from None
        except urllib.error.URLError: raise ProviderTransportError("Anthropic inference transport failed",context=context) from None
        except (UnicodeDecodeError,json.JSONDecodeError): raise ProviderResponseError("Anthropic inference response was not valid JSON",context=context) from None
        content=_extract_content(raw,context); usage=_extract_usage(raw,context)
        reported=_extract_reported_model(raw,context)
        telemetry=InferenceTelemetry(request.request_id,execution_id,self.provider_id,self.spec.id,started_at,int((time.monotonic()-started)*1000),True)
        return InferenceResponse(model=self.spec.id,provider=self.provider_id,content=content,raw=raw,request_id=request.request_id,execution_id=execution_id,
            requested_model=request.model,selected_model=selected,provider_model=self.spec.id,reported_model=reported,usage=usage,telemetry=telemetry)

class AnthropicModelDiscoveryProvider:
    provider_id="anthropic"
    def __init__(self,api_key:str|None=None,base_url:str|None=None,timeout:float=30.0):
        self.api_key=api_key or os.getenv("ANTHROPIC_API_KEY"); self.base_url=(base_url or os.getenv("ANTHROPIC_API_ENDPOINT","https://api.anthropic.com/v1")).rstrip("/"); self.timeout=timeout
    def discover(self)->Sequence[DiscoveredModelCandidate]:
        if not self.api_key: raise RuntimeError("Anthropic model discovery requires ANTHROPIC_API_KEY")
        req=urllib.request.Request(self.base_url+"/models",headers={"x-api-key":str(self.api_key),"anthropic-version":"2023-06-01","accept":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response: raw=json.loads(response.read().decode())
        except urllib.error.HTTPError as exc: raise RuntimeError(f"Anthropic model discovery returned HTTP {exc.code}") from None
        except (TimeoutError,socket.timeout): raise RuntimeError("Anthropic model discovery timed out") from None
        except urllib.error.URLError: raise RuntimeError("Anthropic model discovery transport failed") from None
        except (UnicodeDecodeError,json.JSONDecodeError): raise RuntimeError("Anthropic model discovery returned invalid JSON") from None
        data=raw.get("data") if isinstance(raw,dict) else None
        if not isinstance(data,list): raise RuntimeError("Anthropic model discovery response missing data list")
        out=[]; seen=set()
        for item in data:
            if not isinstance(item,dict): continue
            model_id=str(item.get("id") or "").strip()
            if not model_id or model_id in seen: continue
            seen.add(model_id)
            context_window=item.get("max_input_tokens")
            context_window=int(context_window) if isinstance(context_window,int) and context_window>0 else None
            out.append(DiscoveredModelCandidate(
                candidate_id=f"anthropic:{model_id}",provider_id=self.provider_id,
                model=ModelSpec(model_id,self.provider_id,self.base_url,("chat","reasoning"),context_window,True),
                metadata={"display_name":item.get("display_name"),"created_at":item.get("created_at"),"max_tokens":item.get("max_tokens")},
                discovered_at=now_utc()))
        return tuple(out)

def _extract_content(raw:object,context:ProviderErrorContext)->str:
    if not isinstance(raw,dict): raise ProviderResponseError("Anthropic response body must be a JSON object",context=context)
    blocks=raw.get("content")
    if isinstance(blocks,list):
        parts=[item.get("text","") for item in blocks if isinstance(item,dict) and item.get("type")=="text" and isinstance(item.get("text"),str)]
        if parts: return "\n".join(parts)
    raise ProviderResponseError("Anthropic response did not contain text content",context=context)

def _extract_usage(raw:object,context:ProviderErrorContext)->InferenceUsage:
    usage=raw.get("usage") if isinstance(raw,dict) else None
    if usage is None: return InferenceUsage()
    if not isinstance(usage,dict): raise ProviderResponseError("Anthropic response usage must be an object",context=context)
    return InferenceUsage(_as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens")),
        None if _as_int(usage.get("input_tokens")) is None or _as_int(usage.get("output_tokens")) is None else _as_int(usage.get("input_tokens"))+_as_int(usage.get("output_tokens")))

def _extract_reported_model(raw:object,context:ProviderErrorContext)->str|None:
    value=raw.get("model") if isinstance(raw,dict) else None
    return value.strip() if isinstance(value,str) and value.strip() else None
def _as_int(value:object)->int|None:
    if isinstance(value,bool): return None
    if isinstance(value,int): return value
    if isinstance(value,float) and value.is_integer(): return int(value)
    return None
