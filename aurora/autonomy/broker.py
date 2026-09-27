from .acquisition import AcquisitionError, AcquisitionManager
from .models import CapabilityRequest, CapabilityResult
from .policy import Policy
from .registry import ToolSpec

class CapabilityBroker:
    """Policy boundary between agents and concrete capabilities."""

    def __init__(
        self,
        policy: Policy,
        registry: dict[str, ToolSpec] | None = None,
        acquisition: AcquisitionManager | None = None,
    ):
        self.policy = policy
        self.registry = registry or {}
        self.acquisition = acquisition

    def authorize(self, request: CapabilityRequest) -> CapabilityResult:
        if not self.policy.allows_capability(request.name):
            return CapabilityResult(request.name, "denied", verification="policy")

        spec = self._resolve(request)
        if spec is None:
            if self.registry:
                return CapabilityResult(request.name, "unresolved", verification="registry")
            return CapabilityResult(request.name, "unresolved", verification="registry")

        if not self.policy.allows_source(spec.source):
            return CapabilityResult(
                request.name, "blocked_source", source=spec.source, verification="policy"
            )

        verification = "required" if self.policy.require_verification else "not_required"
        return CapabilityResult(
            request.name,
            "authorized",
            source=spec.source,
            verification=verification,
        )

    def ensure(self, request: CapabilityRequest) -> CapabilityResult:
        """Authorize, acquire if necessary, and return auditable provenance."""
        authorized = self.authorize(request)
        if authorized.status != "authorized":
            return authorized
        spec = self._resolve(request)
        if spec is None:
            return CapabilityResult(request.name, "unresolved", verification="registry")

        try:
            result = self.acquisition.acquire(spec) if self.acquisition else None
        except AcquisitionError:
            return CapabilityResult(
                request.name,
                "acquisition_failed",
                source=spec.source,
                verification="failed",
            )

        if result is None:
            return CapabilityResult(
                request.name,
                "authorized",
                source=spec.source,
                verification="pending",
            )

        return CapabilityResult(
            request.name,
            result.status,
            version=result.version,
            source=result.source,
            verification=result.verification,
            path=result.path,
            sha256=result.sha256,
        )

    def resolve(self, request: CapabilityRequest) -> ToolSpec | None:
        """Resolve a request to the registered tool specification."""
        tool_name = request.name.split(":", 1)[1] if ":" in request.name else request.name
        return self.registry.get(tool_name)

    def _resolve(self, request: CapabilityRequest) -> ToolSpec | None:
        return self.resolve(request)
