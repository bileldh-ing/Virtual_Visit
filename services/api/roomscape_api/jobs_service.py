"""Persisted job state transitions shared by the API and the worker."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import JobStageEvent, ReconstructionJob
from .state_machine import JobState, check_transition, is_terminal


def now() -> datetime:
    return datetime.now(timezone.utc)


def transition(db: Session, job: ReconstructionJob, new: JobState | str, *, attempt_id: uuid.UUID | None = None,
               data: dict | None = None) -> None:
    """Validate and apply a state change, persisting a stage event in the same transaction."""
    new_state = check_transition(job.state, JobState(new).value)
    job.state = new_state.value
    job.stage_started_at = now()
    if is_terminal(new_state.value):
        job.finished_at = now()
    db.add(JobStageEvent(job_id=job.id, attempt_id=attempt_id, stage=new_state.value, event="state",
                         data=data or {}))


def stage_event(db: Session, job: ReconstructionJob, stage: str, event: str, *, attempt_id=None,
                data: dict | None = None) -> None:
    db.add(JobStageEvent(job_id=job.id, attempt_id=attempt_id, stage=stage, event=event, data=data or {}))
