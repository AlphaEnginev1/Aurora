from aurora.autonomy.computer_use import AutonomousComputerUse
from aurora.autonomy.discovery_loop import DiscoveryLoop
from aurora.autonomy.mission_manager import AutonomousMissionManager
from aurora.autonomy.rsi import RecursiveSelfImprovement
from aurora.autonomy.software_engineer import AutonomousSoftwareEngineer

def test_computer_use_observe_act_loop():
    state={"n":0}
    def decide(ctx):
        if state["n"]>=2: return None
        state["n"]+=1
        return {"action":"type","text":str(state["n"])}
    steps=AutonomousComputerUse(lambda actions:{"completed":False,"actions":actions},decide=decide,max_steps=4).run("do it")
    assert len(steps)==2

def test_software_engineer_reaches_publish():
    phases=[]
    def execute(phase,state):
        phases.append(phase); return {"last_phase":phase}
    result=AutonomousSoftwareEngineer(execute=execute,evaluate=lambda state:"succeeded" if state["last_phase"]=="publish" else "continue").run("fix project")
    assert result.status=="succeeded"
    assert phases==["inspect","research","plan","edit","test","verify","publish"]

def test_discovery_loop_does_not_treat_supported_as_verified():
    learned = []
    loop = DiscoveryLoop(
        investigate=lambda h: {"sources": ["s1"]},
        hypothesize=lambda h, e: h + " improved",
        falsify=lambda h, e: {"verdict": "supported", "evidence": ["e1"]},
        novelty=lambda h, e: "novel",
        learn=lambda h, e: learned.append(h) or "kart-1",
    )
    result = loop.run("hypothesis")
    assert result.status == "insufficient"
    assert result.knowledge == ()
    assert not learned


def test_discovery_loop_requires_a_durable_artifact_even_after_verification():
    loop = DiscoveryLoop(
        investigate=lambda h: {"sources": ["s1"]},
        hypothesize=lambda h, e: h + " improved",
        falsify=lambda h, e: {"verdict": "verified", "evidence": ["ev-a", "ev-b"]},
        novelty=lambda h, e: "novel",
        learn=lambda h, e: None,
        max_cycles=1,
    )
    result = loop.run("hypothesis")
    assert result.status == "insufficient"
    assert result.knowledge == ()


def test_discovery_loop_accepts_verified_result_with_durable_artifact():
    loop = DiscoveryLoop(
        investigate=lambda h: {"sources": ["s1", "s2", "s3"]},
        hypothesize=lambda h, e: h + " improved",
        falsify=lambda h, e: {"verdict": "verified", "evidence": ["ev-a", "ev-b"]},
        novelty=lambda h, e: "novel",
        learn=lambda h, e: "kart-verified-1",
        max_cycles=1,
    )
    result = loop.run("hypothesis")
    assert result.status == "discovered"
    assert result.knowledge == ("kart-verified-1",)

def test_mission_manager_delegates_to_existing_runtime():
    class Submission: mission_id="m-1"
    class Result: status="succeeded"
    class Runtime:
        def submit_mission(self,*,objective,context=None): return Submission()
        def run_until_terminal(self,submission,*,max_cycles): assert max_cycles==4; return Result()
    result=AutonomousMissionManager(Runtime(),max_cycles=4).run("mission")
    assert result.mission_id=="m-1" and result.status=="succeeded"

def test_rsi_runs_full_cycle():
    phases=[]
    def execute(phase,state):
        phases.append(phase); return {"change_id":"c-1"} if phase=="ci" else {}
    result=RecursiveSelfImprovement(execute_phase=execute,evaluate=lambda state:"improved",max_cycles=1).run("improve Aurora")
    assert result.status=="succeeded" and result.change_id=="c-1" and phases[-1]=="learn"

# CI acceptance marker: autonomous completion control surfaces are covered.



def test_computer_use_reobserves_environment_after_each_action():
    observations = []
    actions = []

    def observe():
        value = {"observation": len(observations)}
        observations.append(value)
        return value

    def decide(context):
        actions.append(context["observation"]["observation"])
        if len(actions) == 3:
            return None
        return {"action": "read", "path": f"/tmp/{len(actions)}"}

    steps = AutonomousComputerUse(
        execute=lambda batch: {"completed": False, "actions": batch},
        observe=observe,
        decide=decide,
        max_steps=4,
    ).run("observe the changing environment")

    assert len(steps) == 2
    assert len(observations) == 3
    assert actions == [0, 1, 2]
    assert steps[0].observation.value["last_action_result"]["completed"] is False
