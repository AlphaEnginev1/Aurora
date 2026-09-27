from aurora.organization import (
    AgentSpec,
    DepartmentSpec,
    AgentSelector,
    OrganizationRegistry,
)


class LearnedScores:
    def __init__(self, scores):
        self.scores = scores

    def agent_scores_for(self, skill_ids):
        return dict(self.scores)


def registry():
    return OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research papers and evidence",
                capabilities=frozenset({"web"}),
                model_preferences=(),
                skill_ids=("research",),
            ),
            "engineer": AgentSpec(
                agent_id="engineer",
                role="engineer",
                department="engineering",
                mission="implement and debug software",
                capabilities=frozenset({"code"}),
                model_preferences=(),
                skill_ids=("engineering",),
            ),
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset({"web"}),
            ),
            "engineering": DepartmentSpec(
                department_id="engineering",
                mission="engineering",
                agent_ids=("engineer",),
                model_preferences=(),
                allowed_capabilities=frozenset({"code"}),
            ),
        },
    )


def test_agent_selector_filters_by_required_skill_and_capability():
    selector = AgentSelector(registry())
    decision = selector.route(
        objective="research papers and evidence",
        required_skill_ids=("research",),
        capability="web",
    )
    assert decision.selected_role == "researcher"
    assert decision.candidates == (("researcher", decision.selected_score),)


def test_agent_selector_uses_trusted_learned_agent_performance():
    org = registry()
    selector = AgentSelector(
        org,
        skill_learning=LearnedScores({"researcher": 0.95, "engineer": 0.10}),
    )
    decision = selector.route(
        objective="research papers and evidence",
        required_skill_ids=("research",),
    )
    assert decision.selected_role == "researcher"
    assert decision.factors["learned_score"] == 0.95


def test_agent_selector_fails_closed_when_no_specialist_is_viable():
    selector = AgentSelector(registry())
    try:
        selector.route(
            objective="research papers",
            required_skill_ids=("unknown-skill",),
        )
    except RuntimeError as exc:
        assert "no viable authorized specialist agent" in str(exc)
    else:
        raise AssertionError("selector unexpectedly selected an unauthorized specialist")
