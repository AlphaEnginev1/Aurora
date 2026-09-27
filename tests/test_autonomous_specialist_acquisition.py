from dataclasses import dataclass
from pathlib import Path

from aurora.skills.acquisition import SkillAcquisitionRuntime
from aurora.skills.acquisition_pipeline import SkillAcquisitionPipeline
from aurora.skills.autonomous_specialist_acquisition import (
    AutonomousSpecialistAcquisition,
    SpecialistAcquisitionPlan,
)
from aurora.skills.models import SkillSpec
from aurora.skills.registry import SkillRegistry


@dataclass(frozen=True)
class Source:
    source_id: str


@dataclass(frozen=True)
class Acquired:
    tool: str


@dataclass(frozen=True)
class SandboxResult:
    returncode: int = 0
    stdout: str = "ok"
    stderr: str = ""
    timed_out: bool = False


class FakeResearch:
    def retrieve(self, request):
        return (Source("src-1"),)


class FakeAcquisition:
    def __init__(self):
        self.calls = []

    def acquire(self, spec):
        self.calls.append(spec.name)
        return Acquired(spec.name)


class FakeSandbox:
    def execute(self, command, *, workspace):
        return SandboxResult()


def test_autonomous_specialist_acquisition_is_evidence_gated(tmp_path):
    runtime = SkillAcquisitionRuntime(tmp_path / "acquisition.db")
    pipeline = SkillAcquisitionPipeline(runtime, tmp_path / "artifacts.db")
    acquisition = FakeAcquisition()
    service = AutonomousSpecialistAcquisition(
        research=FakeResearch(),
        acquisition=acquisition,
        pipeline=pipeline,
        sandbox=FakeSandbox(),
    )
    gap = runtime.detect_gap(
        mission_id="m-auto",
        skill_id="research-specialist",
        reason="missing specialist",
        required_capabilities=("web",),
    )
    skill = SkillSpec(
        "research-specialist", "1.0.0", "Research Specialist",
        "research", "use evidence", frozenset({"web"}),
        ("source",), ("traceability",), tags=("research",),
    )
    plan = SpecialistAcquisitionPlan(
        skill=skill,
        research_urls=("https://example.test/procedure",),
        tools=(),
        validation_command=("python3", "-c", "print('validated')"),
    )
    prepared = service.prepare(gap=gap, plan=plan, registry=SkillRegistry({}))
    assert prepared.evidence.source_ids == ("src-1",)
    assert prepared.evidence.state == "awaiting_approval"
    assert prepared.candidate.status == "validated"
    approved = pipeline.approve(prepared)
    assert approved.status == "approved"
