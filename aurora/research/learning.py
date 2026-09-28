"""Trust-boundary helpers for feeding verified research into adaptive learning.

Research retrieval and claim extraction are observations. Only durable, verified
knowledge artifacts may become learning input for specialist skills or model
routing.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import EvidenceState
from .store import ResearchStore


class ResearchLearningError(ValueError):
    """Raised when research cannot be promoted into a learning signal."""


@dataclass(frozen=True)
class ResearchLearningSignal:
    """Immutable authorization to use verified research as learning evidence."""

    artifact_ids: tuple[str, ...]
    state: EvidenceState = EvidenceState.VERIFIED

    def __post_init__(self) -> None:
        if self.state is not EvidenceState.VERIFIED:
            raise ResearchLearningError("learning signals must be VERIFIED")
        if not self.artifact_ids:
            raise ResearchLearningError("learning signal requires trusted artifacts")


class ResearchLearningGate:
    """Validate persisted trusted knowledge before it reaches adaptive learning."""

    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def authorize(self, artifact_ids: tuple[str, ...]) -> ResearchLearningSignal:
        normalized = tuple(dict.fromkeys(item.strip() for item in artifact_ids if item.strip()))
        if not normalized:
            raise ResearchLearningError("no trusted knowledge artifacts supplied")

        for artifact_id in normalized:
            artifact = self.store.knowledge_artifact(artifact_id)
            if artifact is None:
                raise ResearchLearningError(
                    f"unknown knowledge artifact: {artifact_id}"
                )
            if artifact.state is not EvidenceState.VERIFIED:
                raise ResearchLearningError(
                    f"knowledge artifact is not verified: {artifact_id}"
                )

            evidence_by_source = {
                source_id: self.store.evidence_for_source(source_id)
                for source_id in artifact.source_ids
            }
            evidence = {
                item.evidence_id
                for items in evidence_by_source.values()
                for item in items
                if item.state is EvidenceState.VERIFIED
            }
            if not set(artifact.evidence_ids).issubset(evidence):
                raise ResearchLearningError(
                    f"knowledge artifact lacks fully verified evidence: {artifact_id}"
                )

        return ResearchLearningSignal(artifact_ids=normalized)

    def context_for(self, artifact_ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        """Return verified artifact claims for governed model-task context."""
        signal = self.authorize(artifact_ids)
        context: list[tuple[str, str]] = []
        for artifact_id in signal.artifact_ids:
            artifact = self.store.knowledge_artifact(artifact_id)
            if artifact is None:
                raise ResearchLearningError(f"unknown trusted knowledge artifact: {artifact_id}")
            context.append((artifact.artifact_id, artifact.claim))
        return tuple(context)


__all__ = [
    "ResearchLearningError",
    "ResearchLearningGate",
    "ResearchLearningSignal",
]
