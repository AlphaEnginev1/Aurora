from .models import CapabilityRequest, CapabilityResult
from .policy import Policy
from .registry import ToolSpec

class CapabilityBroker:
    def __init__(self, policy: Policy, registry: dict[str, ToolSpec] | None = None):
        self.policy = policy
        self.registry = registry or {}

    def authorize(self, request: CapabilityRequest) -> CapabilityResult:
        if not self.policy.allows_capability(request.name):
            return CapabilityResult(request.name, "denied", verification="policy")
        tool_name = request.name.split(":", 1)[1] if ":" in request.name else request.name
        spec = self.registry.get(tool_name)
        if spec is None:
            if self.registry:
                return CapabilityResult(request.name, "unresolved", verification="registry")
            return CapabilityResult(request.name, "authorized", verification="pending")
        if not self.policy.allows_source(spec.source):
            return CapabilityResult(request.name, "blocked_source", source=spec.source, verification="policy")
        verification = "required" if self.policy.require_verification else "not_required"
        return CapabilityResult(request.name, "authorized", source=spec.source, verification=verification)
