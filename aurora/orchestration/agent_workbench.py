"""Universal Workbench tool loop for specialist agents.

Agents receive one provider-neutral tool protocol so every reasoning agent can
request governed Workbench capabilities at runtime instead of relying only on
Director-preplanned capability tasks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aurora.inference.multimodal import MultimodalContentError, parse_content
from aurora.workbench import UniversalWorkbench, WorkbenchRequest, WorkbenchResult


class AgentWorkbenchToolError(RuntimeError):
    """Raised when a model emits an invalid Workbench tool request."""


@dataclass(frozen=True)
class AgentWorkbenchCall:
    capability: str
    arguments: tuple[str, ...] = ()
    options: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.options is None:
            object.__setattr__(self, "options", {})


class AgentWorkbenchToolLoop:
    """Execute model-requested Workbench calls with bounded re-inference."""

    MAX_CALLS = 8
    MAX_RESULT_CHARS = 12000

    INSTRUCTION = """Aurora Universal Workbench is available to you during this task.
When you need real external work, use the Workbench instead of guessing.
Return ONLY this JSON shape when you need one or more Workbench calls:
{"workbench_calls":[{"capability":"workbench:...","arguments":["string"],"options":{}}]}
Rules:
- Use only the registered workbench capabilities listed below.
- arguments must be plain strings.
- options must be a JSON object.
- Never invent results, files, web content, connector responses, or execution state.
- After Aurora returns WORKBENCH_RESULTS, use that evidence and continue reasoning.
- You may issue multiple calls in one turn when they are independent.
- Keep calls minimal and directly related to the task.
Registered capabilities:
- workbench:census
- workbench:preflight
- workbench:capability:resolve
- workbench:command — execute one registered capability inside Aurora's governed sandbox (never an arbitrary executable)
- workbench:research:discover
- workbench:research:retrieve
- workbench:research:retrieve_extract
- workbench:file:list
- workbench:file:read
- workbench:file:write
- workbench:artifact:intake
- workbench:artifact:inspect
- workbench:document:extract
- workbench:github:get
- workbench:github:branch
- workbench:github:file:write
- workbench:github:pr
- workbench:github:merge
- workbench:github:workflow:read
- workbench:project:inspect
- workbench:project:register
- workbench:project:register_github
- workbench:connector:census
- workbench:connector:request
- workbench:browser
- workbench:mcp:list_tools
- workbench:mcp:call
- workbench:computer
If no Workbench action is needed, answer normally.
"""

    def __init__(self, workbench: UniversalWorkbench, *, max_calls: int = MAX_CALLS) -> None:
        if max_calls < 1 or max_calls > self.MAX_CALLS:
            raise AgentWorkbenchToolError("max_calls must be between 1 and 8")
        self.workbench = workbench
        self.max_calls = max_calls

    def run(
        self,
        *,
        request,
        actor: str,
        mission_id: str,
        reason: str,
        task_id: str | None = None,
        preferred_models: Sequence[str] = (),
        router,
        capability: str = "reasoning",
    ):
        messages = list(request.messages)
        if not any(
            message.get("role") == "system"
            and self.INSTRUCTION in str(message.get("content", ""))
            for message in messages
        ):
            messages.insert(0, {"role": "system", "content": self.INSTRUCTION})
        executed = 0
        while True:
            current_metadata = dict(request.metadata or {})
            current_metadata["workbench_tool_loop"] = True
            current_metadata["workbench_calls_executed"] = executed
            current_request = request.__class__(
                messages=messages,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                metadata=current_metadata,
                request_id=request.request_id,
            )
            if preferred_models:
                response = router.generate(
                    current_request,
                    capability=capability,
                    preferred_models=preferred_models,
                )
            else:
                response = router.generate(
                    current_request,
                    capability=capability,
                )
            calls = parse_workbench_calls(response.content)
            if not calls:
                return response
            if executed + len(calls) > self.max_calls:
                raise AgentWorkbenchToolError("Workbench tool-call budget exceeded")
            results: list[dict[str, Any]] = []
            for call in calls:
                # These fields belong to the runtime, not the model. In particular,
                # never let a model-controlled idempotency_key alter the durable
                # request fingerprint or redirect an action replay.
                call_options = dict(call.options or {})
                call_options.pop("idempotency_key", None)
                call_options.pop("work_id", None)
                call_options["work_id"] = mission_id
                idempotency_key = (
                    f"agent-task:{mission_id}:{task_id}:call:{executed}"
                    if task_id and getattr(self.workbench, "session_store", None) is not None
                    else None
                )
                result = self.workbench.execute(
                    WorkbenchRequest(
                        capability=call.capability,
                        actor=actor,
                        mission_id=mission_id,
                        reason=reason,
                        arguments=call.arguments,
                        options=call_options,
                        idempotency_key=idempotency_key,
                    )
                )
                executed += 1
                results.append(_bounded_result(call, result, self.MAX_RESULT_CHARS))
            evidence_content: list[dict[str, Any]] = [
                {
                    "modality": "text",
                    "text": (
                        "WORKBENCH_RESULTS (authoritative Aurora execution evidence):\n"
                        + json.dumps(results, ensure_ascii=False, sort_keys=True, default=str)
                        + "\nContinue the task using only this evidence."
                    ),
                }
            ]
            for result in results:
                _append_media_parts(evidence_content, result.get("output"))
            messages.extend(
                (
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": evidence_content},
                )
            )


def parse_workbench_calls(content: str) -> tuple[AgentWorkbenchCall, ...]:
    raw = str(content or "").strip()
    fence = chr(96) * 3
    if raw.startswith(fence):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith(fence):
            raw = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, dict) or "workbench_calls" not in payload:
        return ()
    rows = payload.get("workbench_calls")
    if not isinstance(rows, list) or not rows or len(rows) > 8:
        raise AgentWorkbenchToolError("workbench_calls must contain 1 to 8 calls")
    calls: list[AgentWorkbenchCall] = []
    for row in rows:
        if not isinstance(row, dict):
            raise AgentWorkbenchToolError("each workbench call must be an object")
        capability = row.get("capability")
        arguments = row.get("arguments", [])
        options = row.get("options", {})
        if not isinstance(capability, str) or not capability.startswith("workbench:"):
            raise AgentWorkbenchToolError("Workbench capability must start with workbench:")
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            raise AgentWorkbenchToolError("Workbench arguments must be a string array")
        if not isinstance(options, dict):
            raise AgentWorkbenchToolError("Workbench options must be an object")
        calls.append(
            AgentWorkbenchCall(
                capability=capability.strip(),
                arguments=tuple(arguments),
                options=options,
            )
        )
    return tuple(calls)


def _bounded_result(
    call: AgentWorkbenchCall,
    result: WorkbenchResult,
    max_chars: int,
) -> dict[str, Any]:
    payload = {
        "capability": call.capability,
        "operation": result.operation,
        "status": result.status,
        "output": result.output,
        "artifact_id": result.artifact_id,
        "provenance": result.provenance,
        "error": result.error,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) <= max_chars:
        return payload
    return {
        "capability": call.capability,
        "operation": result.operation,
        "status": result.status,
        "artifact_id": result.artifact_id,
        "provenance": result.provenance,
        "error": result.error,
        "output": "[truncated: Workbench result exceeded context safety limit]",
    }


__all__ = [
    "AgentWorkbenchCall",
    "AgentWorkbenchToolError",
    "AgentWorkbenchToolLoop",
    "parse_workbench_calls",
]


def _append_media_parts(target: list[dict[str, Any]], output: Any) -> None:
    """Attach embedded media evidence as native multimodal message parts."""
    candidates: list[Any] = []
    if isinstance(output, Mapping):
        candidates.append(output)
        nested = output.get("results")
        if isinstance(nested, list):
            candidates.extend(nested)
    elif isinstance(output, list):
        candidates.extend(output)
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        mime_type = item.get("mime_type") or item.get("mimeType")
        data = item.get("data")
        if not isinstance(mime_type, str) or not isinstance(data, str):
            continue
        modality = (
            "image" if mime_type.startswith("image/")
            else "audio" if mime_type.startswith("audio/")
            else "video" if mime_type.startswith("video/")
            else None
        )
        if modality is None:
            continue
        part = {"modality": modality, "mime_type": mime_type, "data": data}
        try:
            parse_content([part])
        except MultimodalContentError:
            continue
        target.append(part)
