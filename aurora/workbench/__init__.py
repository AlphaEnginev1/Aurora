"""Universal Work / Cowork capability plane for Aurora."""

from .models import WorkbenchRequest, WorkbenchResult, WorkbenchCapability
from .local_computer import LocalComputerError, LocalComputerGateway
from .mcp import MCPBridgeError, MCPClientBridge, MCPRegistry, MCPServerSpec
from .artifacts import ArtifactDescriptor, ArtifactError, ArtifactInspector
from .session import WorkSession, WorkSessionStore
from .project_intake import ProjectIntakeError, ProjectIntakeReport, ProjectIntakeService, ProjectSource, ProjectSourceGraph
from .connectors import ConnectorClient, ConnectorError, ConnectorRegistry, ConnectorSpec
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
    "ProjectIntakeError",
    "ProjectIntakeReport",
    "ProjectIntakeService",
    "ProjectSource",
    "ProjectSourceGraph",
    "ConnectorClient",
    "ConnectorError",
    "ConnectorRegistry",
    "ConnectorSpec",
]
