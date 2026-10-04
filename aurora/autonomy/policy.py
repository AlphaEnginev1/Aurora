from dataclasses import dataclass

@dataclass(frozen=True)
class Policy:
    allowed_capabilities: frozenset[str]
    allowed_sources: frozenset[str]
    require_verification: bool = True

    def allows_capability(self, name: str) -> bool:
        if name in self.allowed_capabilities:
            return True
        return "workbench:*" in self.allowed_capabilities and name.startswith("workbench:")

    def allows_source(self, source: str) -> bool:
        return source in self.allowed_sources
