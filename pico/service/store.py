"""Transactional jobs, leases, fenced attempts and ordered events.

All ownership decisions use PostgreSQL time after taking the job row lock.
No model call or subprocess runs inside a database transaction.
"""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

metadata = MetaData()
jobs = Table(
    "runtime_jobs",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("request_hash", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("state", String(24), nullable=False),
    Column("generation", Integer, nullable=False, default=0),
    Column("owner", String(100)),
    Column("lease_until", DateTime(timezone=True)),
    Column("event_seq", Integer, nullable=False, default=0),
    Column("checkpoint", JSON),
    Column("result", JSON),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)
attempts = Table(
    "runtime_attempts",
    metadata,
    Column("job_id", ForeignKey(jobs.c.id), primary_key=True),
    Column("generation", Integer, primary_key=True),
    Column("worker", String(100), nullable=False),
    Column("state", String(24), nullable=False),
    Column("workspace", Text),
    Column("started_at", DateTime(timezone=True), server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
)
events = Table(
    "runtime_events",
    metadata,
    Column("job_id", ForeignKey(jobs.c.id), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("generation", Integer, nullable=False),
    Column("kind", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)
ACTIVE = ("running", "verifying", "cancelling")
TERMINAL = ("succeeded", "failed", "cancelled")


class Conflict(ValueError):
    pass


class LeaseLost(RuntimeError):
    pass


class CancellationRequested(RuntimeError):
    pass


class Store:
    def __init__(self, url, *, max_attempts=3):
        self.engine = create_engine(url, pool_pre_ping=True)
        if self.engine.dialect.name != "postgresql":
            raise ValueError("The task service requires PostgreSQL")
        self.max_attempts = max_attempts

    def initialize(self):
        metadata.create_all(self.engine)

    @staticmethod
    def _now(connection):
        return connection.scalar(select(func.clock_timestamp()))

    def _event(self, connection, job, kind, payload=None):
        job["event_seq"] += 1
        connection.execute(
            update(jobs)
            .where(jobs.c.id == job["id"])
            .values(event_seq=job["event_seq"])
        )
        connection.execute(
            insert(events).values(
                job_id=job["id"],
                seq=job["event_seq"],
                generation=job["generation"],
                kind=kind,
                payload=payload or {},
            )
        )

    def submit(self, key, request, *, base_commit):
        fingerprint = hashlib.sha256(
            json.dumps(
                request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        with self.engine.begin() as connection:
            created = connection.scalar(
                pg_insert(jobs)
                .values(
                    id=uuid4().hex,
                    idempotency_key=key,
                    request_hash=fingerprint,
                    payload={**request, "base_commit": base_commit},
                    state="queued",
                    generation=0,
                    event_seq=0,
                )
                .on_conflict_do_nothing(index_elements=[jobs.c.idempotency_key])
                .returning(jobs.c.id)
            )
            job = dict(
                connection.execute(
                    select(jobs).where(jobs.c.idempotency_key == key).with_for_update()
                )
                .mappings()
                .one()
            )
            if job["request_hash"] != fingerprint:
                raise Conflict("Idempotency key already belongs to different input")
            if created:
                self._event(connection, job, "job.queued")
            return job, bool(created)

    def claim(self, worker, lease_seconds=15):
        with self.engine.begin() as connection:
            candidate = (
                connection.execute(
                    select(jobs)
                    .where(
                        or_(
                            jobs.c.state == "queued",
                            and_(
                                jobs.c.state.in_(ACTIVE),
                                jobs.c.lease_until <= func.clock_timestamp(),
                            ),
                        )
                    )
                    .order_by(jobs.c.created_at, jobs.c.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if candidate is None:
                return None
            job = dict(candidate)
            now = self._now(connection)
            if job["state"] in ACTIVE:
                connection.execute(
                    update(attempts)
                    .where(
                        attempts.c.job_id == job["id"],
                        attempts.c.generation == job["generation"],
                    )
                    .values(state="expired", finished_at=now)
                )
                self._event(connection, job, "attempt.expired")
            if job["state"] == "cancelling" or job["generation"] >= self.max_attempts:
                state = "cancelled" if job["state"] == "cancelling" else "failed"
                result = {"reason": "lease_expired", "process_exit_confirmed": False}
                connection.execute(
                    update(jobs)
                    .where(jobs.c.id == job["id"])
                    .values(
                        state=state,
                        owner=None,
                        lease_until=None,
                        result=result,
                    )
                )
                self._event(connection, job, "job." + state, result)
                return None
            job.update(
                generation=job["generation"] + 1,
                owner=worker,
                state="running",
                lease_until=now + timedelta(seconds=lease_seconds),
            )
            connection.execute(
                update(jobs)
                .where(jobs.c.id == job["id"])
                .values(
                    generation=job["generation"],
                    owner=worker,
                    state="running",
                    lease_until=job["lease_until"],
                )
            )
            connection.execute(
                insert(attempts).values(
                    job_id=job["id"],
                    generation=job["generation"],
                    worker=worker,
                    state="running",
                )
            )
            self._event(
                connection,
                job,
                "attempt.claimed",
                {
                    "worker": worker,
                    "recovery": "resume_verification"
                    if job["checkpoint"]
                    else "restart",
                },
            )
            return job

    def _owned(self, connection, job_id, generation, worker):
        row = (
            connection.execute(
                select(jobs).where(jobs.c.id == job_id).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        job = dict(row) if row else None
        if (
            job is None
            or job["generation"] != generation
            or job["owner"] != worker
            or job["state"] not in ACTIVE
            or job["lease_until"] <= self._now(connection)
        ):
            raise LeaseLost("Attempt no longer owns a valid job lease")
        return job

    def heartbeat(self, job_id, generation, worker, lease_seconds=15):
        with self.engine.begin() as connection:
            job = self._owned(connection, job_id, generation, worker)
            connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    lease_until=self._now(connection) + timedelta(seconds=lease_seconds)
                )
            )
            return job["state"] == "cancelling"

    def workspace(self, job_id, generation, worker, path):
        with self.engine.begin() as connection:
            job = self._owned(connection, job_id, generation, worker)
            if job["state"] == "cancelling":
                raise CancellationRequested("Cancellation requested")
            connection.execute(
                update(attempts)
                .where(
                    attempts.c.job_id == job_id,
                    attempts.c.generation == generation,
                )
                .values(workspace=path)
            )
            self._event(connection, job, "workspace.ready")

    def checkpoint(self, job_id, generation, worker, value):
        with self.engine.begin() as connection:
            job = self._owned(connection, job_id, generation, worker)
            if job["state"] == "cancelling":
                raise CancellationRequested("Cancellation requested")
            connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    checkpoint=value,
                    state="verifying",
                )
            )
            connection.execute(
                update(attempts)
                .where(
                    attempts.c.job_id == job_id,
                    attempts.c.generation == generation,
                )
                .values(state="verifying")
            )
            self._event(
                connection,
                job,
                "checkpoint.sealed",
                {
                    "sha256": value["sha256"],
                    "source_generation": value["source_generation"],
                },
            )
            self._event(connection, job, "verification.started")

    def finish(self, job_id, generation, worker, state, result):
        if state not in TERMINAL:
            raise ValueError("Invalid terminal state")
        with self.engine.begin() as connection:
            job = self._owned(connection, job_id, generation, worker)
            if job["state"] == "cancelling" and state != "cancelled":
                raise CancellationRequested("Cannot publish after cancellation")
            if state == "succeeded" and (
                job["state"] != "verifying"
                or result.get("verification_passed") is not True
            ):
                raise Conflict("Success requires completed verification")
            connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    state=state,
                    result=result,
                    owner=None,
                    lease_until=None,
                )
            )
            connection.execute(
                update(attempts)
                .where(
                    attempts.c.job_id == job_id,
                    attempts.c.generation == generation,
                )
                .values(state=state, finished_at=self._now(connection))
            )
            self._event(connection, job, "job." + state, result)

    def cancel(self, job_id):
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(jobs).where(jobs.c.id == job_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise KeyError(job_id)
            job = dict(row)
            if job["state"] in TERMINAL or job["state"] == "cancelling":
                return job["state"]
            state = "cancelled" if job["state"] == "queued" else "cancelling"
            connection.execute(
                update(jobs).where(jobs.c.id == job_id).values(state=state)
            )
            self._event(connection, job, "job." + state)
            return state

    def get(self, job_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(jobs).where(jobs.c.id == job_id))
            job = row.mappings().first()
            return dict(job) if job else None

    def list_jobs(self):
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(jobs).order_by(jobs.c.created_at.desc()).limit(100)
                ).mappings()
            ]

    def list_attempts(self, job_id):
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(attempts)
                    .where(attempts.c.job_id == job_id)
                    .order_by(attempts.c.generation)
                ).mappings()
            ]

    def read_events(self, job_id, after=0):
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(events)
                    .where(
                        events.c.job_id == job_id,
                        events.c.seq > after,
                    )
                    .order_by(events.c.seq)
                    .limit(100)
                ).mappings()
            ]
