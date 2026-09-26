class AcquisitionManager:
    """Policy-driven acquisition facade; installers remain isolated from the broker."""
    def plan(self, tool_spec):
        return {"tool": tool_spec.name, "source": tool_spec.source, "action": "install"}
