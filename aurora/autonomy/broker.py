from .models import CapabilityRequest, CapabilityResult
from .policy import Policy

class CapabilityBroker:
    def __init__(self, policy: Policy):
        self.policy = policy

    def authorize(self, request: CapabilityRequest) -> CapabilityResult:
        if not self.policy.allows_capability(request.name):
            return CapabilityResult(request.name, "denied", verification="policy")
        return CapabilityResult(request.name, "authorized", verification="pending")
