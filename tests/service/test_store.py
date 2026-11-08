from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from pico.service.store import CancellationRequested, Conflict, LeaseLost


def submit(store, key="task-1"):
    return store.submit(
        key, {"repository_id": "demo", "task": "Fix addition"}, base_commit="a" * 40
    )[0]


def expire(store, job_id):
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE runtime_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": job_id},
        )


def checkpoint(store, job, worker):
    store.checkpoint(
        job["id"],
        job["generation"],
        worker,
        {
            "sha256": "a" * 64,
            "source_generation": job["generation"],
        },
    )


def test_idempotency_and_input_conflict(store):
    first = submit(store)
    assert submit(store)["id"] == first["id"]
    with pytest.raises(Conflict):
        store.submit(
            "task-1",
            {"repository_id": "demo", "task": "Different request"},
            base_commit="a" * 40,
        )
    assert len(store.read_events(first["id"])) == 1


def test_concurrent_duplicate_submission(store):
    barrier = Barrier(2)

    def invoke(_):
        barrier.wait()
        return submit(store)["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(invoke, range(2)))
    assert values[0] == values[1]
    assert len(store.list_jobs()) == 1


def test_two_workers_cannot_claim_same_live_attempt(store):
    submit(store)
    barrier = Barrier(2)

    def invoke(name):
        barrier.wait()
        return store.claim(name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(invoke, ["one", "two"]))
    assert sum(job is not None for job in claims) == 1


def test_expired_owner_cannot_resurrect_or_publish(store):
    job = submit(store)
    old = store.claim("old")
    expire(store, job["id"])
    with pytest.raises(LeaseLost):
        store.heartbeat(job["id"], old["generation"], "old")
    new = store.claim("new")
    assert new["generation"] == 2
    with pytest.raises(LeaseLost):
        store.finish(job["id"], 1, "old", "failed", {})
    checkpoint(store, new, "new")
    store.finish(job["id"], 2, "new", "succeeded", {"verification_passed": True})
    assert store.get(job["id"])["state"] == "succeeded"
    assert [attempt["state"] for attempt in store.list_attempts(job["id"])] == [
        "expired",
        "succeeded",
    ]


def test_success_requires_verification_phase_and_result(store):
    job = submit(store)
    claim = store.claim("worker")
    with pytest.raises(Conflict):
        store.finish(job["id"], 1, "worker", "succeeded", {"verification_passed": True})
    checkpoint(store, claim, "worker")
    with pytest.raises(Conflict):
        store.finish(
            job["id"], 1, "worker", "succeeded", {"verification_passed": False}
        )


def test_cancel_before_claim_and_after_success(store):
    job = submit(store)
    assert store.cancel(job["id"]) == "cancelled"
    assert store.claim("worker") is None
    assert store.cancel(job["id"]) == "cancelled"
    other = submit(store, "other")
    claim = store.claim("worker")
    checkpoint(store, claim, "worker")
    store.finish(other["id"], 1, "worker", "succeeded", {"verification_passed": True})
    assert store.cancel(other["id"]) == "succeeded"


def test_cancel_wins_before_late_success(store):
    job = submit(store)
    claim = store.claim("worker")
    checkpoint(store, claim, "worker")
    assert store.cancel(job["id"]) == "cancelling"
    assert store.heartbeat(job["id"], 1, "worker") is True
    with pytest.raises(CancellationRequested):
        store.finish(job["id"], 1, "worker", "succeeded", {"verification_passed": True})
    store.finish(job["id"], 1, "worker", "cancelled", {"process_exit_confirmed": True})
    assert store.get(job["id"])["state"] == "cancelled"


def test_expired_cancelled_worker_is_not_restarted(store):
    job = submit(store)
    store.claim("worker")
    store.cancel(job["id"])
    expire(store, job["id"])
    assert store.claim("replacement") is None
    result = store.get(job["id"])
    assert result["state"] == "cancelled"
    assert result["result"]["process_exit_confirmed"] is False


def test_attempt_limit_finishes_job_and_keeps_history(store):
    store.max_attempts = 1
    job = submit(store)
    store.claim("worker")
    expire(store, job["id"])
    assert store.claim("replacement") is None
    assert store.get(job["id"])["state"] == "failed"
    assert len(store.list_attempts(job["id"])) == 1


def test_events_have_transactional_monotonic_sequence(store):
    job = submit(store)
    claim = store.claim("worker")
    checkpoint(store, claim, "worker")
    store.finish(job["id"], 1, "worker", "succeeded", {"verification_passed": True})
    rows = store.read_events(job["id"])
    assert [event["seq"] for event in rows] == list(range(1, len(rows) + 1))
    assert rows[-1]["kind"] == "job.succeeded"
    assert store.get(job["id"])["event_seq"] == len(rows)
