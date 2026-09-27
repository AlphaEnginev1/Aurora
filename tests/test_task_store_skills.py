from pathlib import Path

from aurora.orchestration.task_engine import DelegatedTask
from aurora.orchestration.task_store import DurableTaskStore


def test_durable_task_store_persists_skill_assignments(tmp_path: Path):
    store=DurableTaskStore(tmp_path/"tasks.db")
    task=DelegatedTask(
        task_id="t",
        role="researcher",
        objective="research",
        skills=("research-literature-analysis","research-source-validation"),
    )
    store.enqueue("mission-1",(task,))
    stored=store.get("mission-1","t")
    assert stored is not None
    assert stored.task.skills == task.skills
    events=store.events("mission-1")
    assert events[0].event_type=="enqueued"
    assert events[0].payload["skills"] == list(task.skills)
    claimed=store.claim_next("mission-1","worker-1")
    assert claimed is not None
    assert claimed.task.skills == task.skills
