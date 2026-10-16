import json
from types import SimpleNamespace

from aurora.autonomy.runtime_bindings import build_autonomy_runtime_bindings


class ComputerRouter:
    def __init__(self, actions):
        self.actions = list(actions)
        self.index = 0

    def generate(self, request, capability=None):
        prompt = request.messages[-1]["content"]
        if "Choose at most one safe next action" in prompt:
            action = self.actions[self.index]
            self.index += 1
            content = json.dumps({"action": action}) if action is not None else '{"done": true}'
        else:
            content = "{}"
        return SimpleNamespace(content=content)


class ComputerWorkbench:
    def __init__(self, *, durable=True):
        self.calls = []
        if durable:
            self.session_store = object()

    def execute(self, request):
        self.calls.append(request)
        if request.capability == "workbench:census":
            return SimpleNamespace(
                status="succeeded",
                error=None,
                output=[{"capability_id": "workbench:computer", "available": True}],
            )
        return SimpleNamespace(
            status="succeeded",
            error=None,
            output={"completed": True, "action_count": 1},
        )


def _controller(workbench, actions):
    controllers = build_autonomy_runtime_bindings(
        director_service=SimpleNamespace(),
        router=ComputerRouter(actions),
        workbench=workbench,
        research_service=None,
        skill_learning=None,
    )
    return controllers["computer_use"]


def test_computer_action_uses_stable_durable_key_across_retries():
    action = {
        "objective": "controlled click",
        "action": "click",
        "x": 10,
        "y": 20,
    }
    workbench = ComputerWorkbench(durable=True)
    controller = _controller(workbench, [action, action])

    first = controller.run("controlled computer idempotency test")
    second = controller.run("controlled computer idempotency test")

    assert len(first) == len(second) == 1
    computer_calls = [
        request for request in workbench.calls
        if request.capability == "workbench:computer"
    ]
    assert len(computer_calls) == 2
    first_request, second_request = computer_calls
    assert first_request.mission_id == second_request.mission_id
    assert first_request.idempotency_key
    assert first_request.idempotency_key == second_request.idempotency_key
    assert first_request.idempotency_key.startswith("autonomy-computer:")
    assert first_request.options["work_id"] == first_request.mission_id


def test_computer_action_key_changes_when_action_payload_changes():
    first_action = {
        "objective": "controlled click",
        "action": "click",
        "x": 10,
        "y": 20,
    }
    second_action = {**first_action, "x": 11}
    workbench = ComputerWorkbench(durable=True)
    controller = _controller(workbench, [first_action, second_action])

    controller.run("controlled computer idempotency test")
    controller.run("controlled computer idempotency test")

    keys = [
        request.idempotency_key for request in workbench.calls
        if request.capability == "workbench:computer"
    ]
    assert len(keys) == 2
    assert keys[0] != keys[1]


def test_computer_action_does_not_request_durable_key_without_session_store():
    action = {
        "objective": "controlled click",
        "action": "click",
        "x": 10,
        "y": 20,
    }
    workbench = ComputerWorkbench(durable=False)
    controller = _controller(workbench, [action])

    controller.run("controlled computer idempotency test")

    computer_request = next(
        request for request in workbench.calls
        if request.capability == "workbench:computer"
    )
    assert computer_request.idempotency_key is None
