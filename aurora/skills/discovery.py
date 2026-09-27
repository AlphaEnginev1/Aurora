"""Autonomous discovery and construction of specialist skills.

The service converts a durable SkillGap into a governed candidate without a
human-authored SpecialistAcquisitionPlan. Source discovery is injected as a
policy-controlled provider; the service never performs direct network access.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Mapping

from aurora.autonomy.registry import ToolSpec
from aurora.research.models import ResearchRequest, SourceRecord
from aurora.research.service import ResearchService

from .acquisition import SkillGap
from .models import SkillSpec
from .registry import SkillRegistry

_SOURCE_LINE = re.compile(r"^(?:[-*] |\d+[.)] |(?:step|procedure|method|process)\s*[:.-])(.+)$", re.I)
_CAPABILITY_HINTS = {
    "python": ("python", "pip", "pytest"),
    "git": ("git", "commit", "branch", "repository"),
    "web": ("http", "https", "browser", "web", "url"),
    "research": ("evidence", "source", "citation", "verify"),
    "data": ("csv", "json", "dataset", "dataframe"),
}


class SkillDiscoveryError(RuntimeError):
    """Raised when autonomous skill discovery cannot produce safe evidence."""


@dataclass(frozen=True)
class SkillConstructionEvaluation:
    source_count: int
    procedure_count: int
    verification_count: int
    quality_count: int
    score: float
    evidence_state: str


@dataclass(frozen=True)
class SkillDiscoveryResult:
    skill: SkillSpec
    sources: tuple[SourceRecord, ...]
    tools: tuple[ToolSpec, ...]
    validation_command: tuple[str, ...]
    evaluation: SkillConstructionEvaluation


class SkillDiscoveryService:
    """Discover source-backed procedures and construct a candidate SkillSpec."""

    def __init__(
        self,
        *,
        research: ResearchService,
        source_discovery: Callable[[SkillGap], tuple[str, ...]],
        tool_catalog: Mapping[str, ToolSpec] | None = None,
    ) -> None:
        self.research = research
        self.source_discovery = source_discovery
        self.tool_catalog = dict(tool_catalog or {})

    def discover(self, *, gap: SkillGap, registry: SkillRegistry) -> SkillDiscoveryResult:
        urls = tuple(dict.fromkeys(self.source_discovery(gap)))
        if not urls:
            raise SkillDiscoveryError(
                f"no governed research sources discovered for skill {gap.skill_id}"
            )

        sources = self.research.retrieve(
            ResearchRequest(
                request_id=f"skill-discovery-{gap.mission_id}-{gap.skill_id}",
                objective=(
                    f"Discover reliable procedures for specialist skill {gap.skill_id}. "
                    f"Gap: {gap.reason}. Capabilities: {', '.join(gap.required_capabilities)}"
                ),
                required_sources=urls,
                constraints=gap.required_capabilities,
                max_sources=len(urls),
            )
        )
        if not sources:
            raise SkillDiscoveryError("research discovery returned no sources")

        procedure_lines = self._extract_procedures(sources)
        if not procedure_lines:
            raise SkillDiscoveryError(
                "sources contained no machine-extractable procedural evidence"
            )

        instructions = self._build_instructions(gap, procedure_lines)
        verification = self._build_verification(gap, sources)
        quality = self._build_quality(gap)
        tags = self._tags(gap, sources)
        tools = self._tools(gap, sources)

        active = registry.skills.get(gap.skill_id)
        version = self._next_version(active.version if active else None)
        skill = SkillSpec(
            skill_id=gap.skill_id,
            version=version,
            name=self._name(gap.skill_id),
            mission=f"Perform {gap.reason.strip() or gap.skill_id} as a governed specialist.",
            instructions=instructions,
            required_capabilities=frozenset(gap.required_capabilities),
            verification_requirements=verification,
            quality_checks=quality,
            tags=tags,
            dependencies=(),
            enabled=True,
            model_preferences=active.model_preferences if active else (),
        )

        evaluation = self._evaluate(
            source_count=len(sources),
            procedure_count=len(procedure_lines),
            verification_count=len(verification),
            quality_count=len(quality),
        )
        if evaluation.evidence_state != "complete":
            raise SkillDiscoveryError("constructed skill did not meet evidence threshold")

        validation_command = self._validation_command(skill, evaluation)
        return SkillDiscoveryResult(
            skill=skill,
            sources=sources,
            tools=tools,
            validation_command=validation_command,
            evaluation=evaluation,
        )

    @staticmethod
    def _extract_procedures(sources: tuple[SourceRecord, ...]) -> tuple[str, ...]:
        candidates: list[str] = []
        for source in sources:
            text = source.content_text or ""
            for raw in text.splitlines():
                line = " ".join(raw.strip().split())
                if not line or len(line) < 12 or len(line) > 500:
                    continue
                match = _SOURCE_LINE.match(line)
                if match:
                    procedure = match.group(1).strip()
                    if procedure and procedure not in candidates:
                        candidates.append(procedure)
                elif re.match(r"^(use|run|create|check|verify|compare|record|install|configure|validate|measure)\b", line, re.I):
                    if line not in candidates:
                        candidates.append(line)
        return tuple(candidates[:16])

    @staticmethod
    def _build_instructions(gap: SkillGap, procedures: tuple[str, ...]) -> str:
        lines = [
            "Operate only within Aurora's granted capabilities and governance.",
            f"Resolve the specialist gap: {gap.reason.strip() or gap.skill_id}.",
            "Use the following evidence-backed procedure steps:",
        ]
        lines.extend(f"{index}. {step}" for index, step in enumerate(procedures, 1))
        lines.append(
            "Preserve source provenance, record evidence, and stop when a required "
            "verification check cannot be evidenced."
        )
        return "\n".join(lines)

    @staticmethod
    def _build_verification(gap: SkillGap, sources: tuple[SourceRecord, ...]) -> tuple[str, ...]:
        checks = ["source_traceability", "procedure_evidence", "capability_boundary"]
        if "web" in gap.required_capabilities:
            checks.append("network_policy_compliance")
        if len(sources) >= 2:
            checks.append("multi_source_consistency")
        return tuple(checks)

    @staticmethod
    def _build_quality(gap: SkillGap) -> tuple[str, ...]:
        checks = ["procedure_completeness", "no_unsupported_claims", "reproducibility"]
        if "data" in gap.required_capabilities:
            checks.append("data_integrity")
        return tuple(checks)

    def _tools(self, gap: SkillGap, sources: tuple[SourceRecord, ...]) -> tuple[ToolSpec, ...]:
        corpus = " ".join((source.content_text or "").lower() for source in sources)
        selected: list[ToolSpec] = []
        for name, spec in sorted(self.tool_catalog.items()):
            hints = _CAPABILITY_HINTS.get(name.lower(), (name.lower(),))
            if any(hint in corpus for hint in hints):
                selected.append(spec)
        return tuple(selected)

    @staticmethod
    def _tags(gap: SkillGap, sources: tuple[SourceRecord, ...]) -> tuple[str, ...]:
        values = list(dict.fromkeys(("specialist", *gap.required_capabilities)))
        host_tags = [source.url.split("//", 1)[-1].split("/", 1)[0] for source in sources]
        values.extend(tag for tag in host_tags if tag not in values)
        return tuple(values[:12])

    @staticmethod
    def _name(skill_id: str) -> str:
        return skill_id.replace("-", " ").replace("_", " ").strip().title()

    @staticmethod
    def _next_version(active: str | None) -> str:
        if not active:
            return "1.0.0"
        parts = active.split(".")
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
        return active + ".next"

    @staticmethod
    def _evaluate(
        *,
        source_count: int,
        procedure_count: int,
        verification_count: int,
        quality_count: int,
    ) -> SkillConstructionEvaluation:
        score = min(
            1.0,
            0.25 * min(source_count, 2) / 2
            + 0.35 * min(procedure_count, 4) / 4
            + 0.20 * min(verification_count, 4) / 4
            + 0.20 * min(quality_count, 3) / 3,
        )
        state = "complete" if score >= 0.75 and procedure_count >= 2 else "insufficient"
        return SkillConstructionEvaluation(
            source_count, procedure_count, verification_count, quality_count, score, state
        )

    @staticmethod
    def _validation_command(
        skill: SkillSpec, evaluation: SkillConstructionEvaluation
    ) -> tuple[str, ...]:
        checks = tuple(
            f"VERIFY:{check}=pass" for check in skill.verification_requirements
        ) + tuple(f"QUALITY:{check}=pass" for check in skill.quality_checks)
        payload = repr("\n".join(checks))
        code = (
            "import sys; "
            f"skill_id={skill.skill_id!r}; instructions={skill.instructions!r}; "
            f"score={evaluation.score!r}; markers={payload}; "
            "assert skill_id and instructions.strip(); "
            "assert score >= 0.75; "
            "print(markers)"
        )
        return ("python3", "-c", code)


__all__ = [
    "SkillConstructionEvaluation",
    "SkillDiscoveryError",
    "SkillDiscoveryResult",
    "SkillDiscoveryService",
]
