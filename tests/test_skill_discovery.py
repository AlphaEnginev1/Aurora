from dataclasses import dataclass
from aurora.skills.acquisition import SkillAcquisitionRuntime
from aurora.skills.discovery import SkillDiscoveryService
from aurora.skills.models import SkillSpec
from aurora.skills.registry import SkillRegistry
from aurora.research.models import SourceRecord

@dataclass(frozen=True)
class FakeResearch:
    def retrieve(self, request):
        return (
            SourceRecord("src-1","https://docs.test/a","now","a",200,content_text="- Run the parser\n- Verify the result against the source\n"),
            SourceRecord("src-2","https://docs.test/b","now","b",200,content_text="- Record provenance\n- Compare independent evidence\n"),
        )

def test_skill_discovery_constructs_candidate_without_manual_plan():
    service=SkillDiscoveryService(
        research=FakeResearch(),
        source_discovery=lambda gap: ("https://docs.test/a","https://docs.test/b"),
    )
    gap=SkillAcquisitionRuntime(__import__("pathlib").Path(":memory:"))
    # The runtime above is only used for the durable gap contract; use a real
    # temporary DB so SQLite initialization remains portable.
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        runtime=SkillAcquisitionRuntime(__import__("pathlib").Path(root)/"gaps.db")
        durable_gap=runtime.detect_gap(
            mission_id="m-discovery",skill_id="data-specialist",
            reason="missing data procedure",required_capabilities=("data",),
        )
        result=service.discover(gap=durable_gap,registry=SkillRegistry({}))
    assert result.skill.skill_id=="data-specialist"
    assert result.skill.version=="1.0.0"
    assert result.evaluation.evidence_state=="complete"
    assert len(result.skill.verification_requirements)>=3
    assert result.validation_command[0]=="python3"
