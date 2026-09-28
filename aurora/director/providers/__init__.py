"""Provider boundary for Aurora's Director role."""
from __future__ import annotations

from typing import Protocol

from ..models import DirectorRequest, DirectorResponse, DirectorToolResult


class DirectorProvider(Protocol):
    """Common adapter contract for any model serving as Aurora Director."""

    provider_id: str

    def generate(
        self,
        request: DirectorRequest,
        *,
        execution_id: str,
        tool_results: tuple[DirectorToolResult, ...] = (),
    ) -> DirectorResponse:
        """Generate a Director turn; tool execution remains Aurora's responsibility."""
        ...
