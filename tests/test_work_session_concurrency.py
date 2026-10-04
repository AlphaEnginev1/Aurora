from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aurora.workbench.session import WorkSessionStore


def test_work_session_store_concurrent_capabilities_and_receipts_are_lossless(tmp_path: Path):
    path = tmp_path / "sessions.db"
    store = WorkSessionStore(path)
    store.create_or_get(
        work_id="mission-concurrent",
        mission_id="mission-concurrent",
        actor="director",
        environment="test",
        workspace_root=str(tmp_path),
    )

    capabilities = tuple(f"workbench:test:{index}" for index in range(12))

    def write(index: int):
        capability = capabilities[index]
        store.record_capability("mission-concurrent", capability)
        store.record_artifact("mission-concurrent", f"artifact-{index}")
        store.record_receipt(
            "mission-concurrent",
            f"receipt-{index}",
            correlation_id=f"corr-{index}",
            payload={"index": index},
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(write, range(len(capabilities))))

    restored = WorkSessionStore(path).get("mission-concurrent")
    receipts = WorkSessionStore(path).get_receipts("mission-concurrent")

    assert set(capabilities).issubset(set(restored.capability_ids))
    assert set(f"artifact-{index}" for index in range(12)).issubset(
        set(restored.artifact_ids)
    )
    assert set(f"receipt-{index}" for index in range(12)).issubset(
        set(restored.execution_receipts)
    )
    assert set(f"corr-{index}" for index in range(12)).issubset(
        set(restored.correlation_ids)
    )
    assert len(receipts) == 12
