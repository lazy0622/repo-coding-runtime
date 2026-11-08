"""Authenticated local job API and reconnectable lifecycle event stream."""

import asyncio
import hashlib
import json
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .config import Settings
from .store import Conflict, Store, TERMINAL
from .worker import git


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_id: str = Field(min_length=1, max_length=100)
    task: str = Field(min_length=5, max_length=16000)


def public_job(job):
    return {
        key: job[key]
        for key in (
            "id",
            "payload",
            "state",
            "generation",
            "result",
            "event_seq",
            "created_at",
        )
    }


def create_app(settings, store=None):
    store = store or Store(settings.database_url)
    store.initialize()
    app = FastAPI(title="Repo Runtime Task Service", version="0.1.0")
    security = HTTPBearer(auto_error=False)

    def authorized(
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ):
        if credentials is None or not secrets.compare_digest(
            credentials.credentials, settings.api_token
        ):
            raise HTTPException(
                401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"}
            )

    def require_job(job_id):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    @app.get("/health")
    def health():
        with store.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "postgresql"}

    @app.get("/api/repositories", dependencies=[Depends(authorized)])
    def repositories():
        return [
            {
                "id": key,
                "sandbox": value.sandbox,
                "executor": "configured_fixture" if value.agent_command else "pico",
            }
            for key, value in settings.repositories.items()
        ]

    @app.post("/api/jobs", dependencies=[Depends(authorized)])
    def submit(
        body: Submission, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        repository = settings.repositories.get(body.repository_id)
        if repository is None:
            raise HTTPException(404, "Repository is not registered")
        base = git(repository.path, "rev-parse", "HEAD").decode().strip()
        try:
            job, created = store.submit(
                idempotency_key, body.model_dump(), base_commit=base
            )
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"created": created, "job": public_job(job)}

    @app.get("/api/jobs", dependencies=[Depends(authorized)])
    def list_jobs():
        return [public_job(job) for job in store.list_jobs()]

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(authorized)])
    def get_job(job_id: str):
        job = require_job(job_id)
        result = public_job(job)
        result["attempts"] = [
            {key: value for key, value in attempt.items() if key != "workspace"}
            for attempt in store.list_attempts(job_id)
        ]
        return result

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(authorized)])
    def cancel(job_id: str):
        require_job(job_id)
        return {"state": store.cancel(job_id)}

    @app.get("/api/jobs/{job_id}/patch", dependencies=[Depends(authorized)])
    def patch(job_id: str):
        checkpoint = require_job(job_id)["checkpoint"]
        if not checkpoint:
            raise HTTPException(409, "No accepted patch checkpoint yet")
        path = (settings.data_dir / checkpoint["patch_path"]).resolve()
        try:
            path.relative_to(settings.data_dir)
        except ValueError as exc:
            raise HTTPException(409, "Invalid artifact path") from exc
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != checkpoint["sha256"]:
            raise HTTPException(409, "Patch digest mismatch")
        return Response(
            data,
            media_type="text/x-diff",
            headers={
                "Content-Disposition": f'attachment; filename="{job_id}.patch"',
            },
        )

    @app.get("/api/jobs/{job_id}/events", dependencies=[Depends(authorized)])
    async def stream_events(
        job_id: str,
        request: Request,
        after: int = Query(0, ge=0),
        follow: bool = True,
        last_event_id: str | None = Header(None),
    ):
        await asyncio.to_thread(require_job, job_id)
        try:
            cursor = max(after, int(last_event_id or 0))
            if cursor < 0:
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(400, "Invalid Last-Event-ID") from exc

        async def generate():
            nonlocal cursor
            while True:
                rows = await asyncio.to_thread(store.read_events, job_id, cursor)
                for event in rows:
                    cursor = event["seq"]
                    yield (
                        f"id: {cursor}\nevent: {event['kind']}\ndata: "
                        + json.dumps(event, default=str, ensure_ascii=False)
                        + "\n\n"
                    )
                job = await asyncio.to_thread(store.get, job_id)
                if not follow or (
                    job["state"] in TERMINAL and cursor >= job["event_seq"]
                ):
                    return
                if await request.is_disconnected():
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def home():
        from pathlib import Path

        return Path(__file__).with_name("workbench.html").read_text(encoding="utf-8")

    return app


def main():
    import uvicorn

    uvicorn.run(create_app(Settings.load()), host="127.0.0.1", port=8010)


if __name__ == "__main__":
    main()
