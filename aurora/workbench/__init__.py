"""Universal Work / Cowork capability plane for Aurora."""

from .models import WorkbenchRequest, WorkbenchResult, WorkbenchCapability
from .local_computer import LocalComputerError, LocalComputerGateway
from .mcp import MCPBridgeError, MCPClientBridge, MCPRegistry, MCPServerSpec
from .artifacts import ArtifactDescriptor, ArtifactError, ArtifactInspector
from .session import WorkSession, WorkSessionStore
from .service import UniversalWorkbench, WorkbenchError

__all__ = [
    "WorkbenchCapability",
    "WorkbenchError",
    "WorkbenchRequest",
    "WorkbenchResult",
    "UniversalWorkbench",
    "MCPBridgeError",
    "MCPClientBridge",
    "MCPRegistry",
    "MCPServerSpec",
    "LocalComputerError",
    "LocalComputerGateway",
    "ArtifactDescriptor",
    "ArtifactError",
    "ArtifactInspector",
    "WorkSession",
    "WorkSessionStore",
]
