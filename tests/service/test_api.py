from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from pico.service.api import create_app
from pico.service.config import Repository, Settings
from pico.service.store import jobs


def client_for(store, tmp_path):
    settings = Settings(
        store.engine.url,
        "token-" + "x" * 30,
        tmp_path,
        {"demo": Repository(Path.cwd(), ["python", "-c", "pass"])},
    )
    client = TestClient(create_app(settings, store))
    client.headers["Authorization"] = "Bearer " + settings.api_token
    return client


def test_authentication_and_submission_idempotency(store, tmp_path):
    client = client_for(store, tmp_path)
    assert (
        client.get("/api/jobs", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    payload = {"repository_id": "demo", "task": "Fix a known issue"}
    first = client.post(
        "/api/jobs", json=payload, headers={"Idempotency-Key": "repeat"}
    )
    second = client.post(
        "/api/jobs", json=payload, headers={"Idempotency-Key": "repeat"}
    )
    assert first.status_code == 200 and first.json()["created"] is True
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    assert second.json()["created"] is False
    assert (
        client.post(
            "/api/jobs",
            json=payload | {"task": "Different input"},
            headers={"Idempotency-Key": "repeat"},
        ).status_code
        == 409
    )


def test_requests_cannot_select_paths_or_commands(store, tmp_path):
    client = client_for(store, tmp_path)
    payload = {"repository_id": "../../secret", "task": "Fix a known issue"}
    assert (
        client.post(
            "/api/jobs", json=payload, headers={"Idempotency-Key": "one"}
        ).status_code
        == 404
    )
    payload = {
        "repository_id": "demo",
        "task": "Fix a known issue",
        "command": "arbitrary",
    }
    assert (
        client.post(
            "/api/jobs", json=payload, headers={"Idempotency-Key": "two"}
        ).status_code
        == 422
    )


def test_events_resume_and_stream_disconnect_does_not_cancel_job(store, tmp_path):
    client = client_for(store, tmp_path)
    job = store.submit(
        "one", {"repository_id": "demo", "task": "Fix issue"}, base_commit="a" * 40
    )[0]
    response = client.get("/api/jobs/" + job["id"] + "/events?follow=false")
    assert response.status_code == 200 and "id: 1" in response.text
    resumed = client.get(
        "/api/jobs/" + job["id"] + "/events?follow=false",
        headers={"Last-Event-ID": "1"},
    )
    assert "job.queued" not in resumed.text
    assert store.get(job["id"])["state"] == "queued"


def test_terminal_event_stream_drains_more_than_one_batch(store, tmp_path):
    client = client_for(store, tmp_path)
    submitted = store.submit(
        "one", {"repository_id": "demo", "task": "Fix issue"}, base_commit="a" * 40
    )[0]
    with store.engine.begin() as connection:
        job = dict(
            connection.execute(
                select(jobs).where(jobs.c.id == submitted["id"]).with_for_update()
            )
            .mappings()
            .one()
        )
        for _ in range(125):
            store._event(connection, job, "test.record")
    store.cancel(submitted["id"])
    response = client.get("/api/jobs/" + submitted["id"] + "/events")
    assert response.status_code == 200
    assert response.text.count("\nid: ") + response.text.startswith("id: ") == 127
    assert "job.cancelled" in response.text
