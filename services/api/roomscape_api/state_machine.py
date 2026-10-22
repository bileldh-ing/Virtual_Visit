"""Reconstruction job state machine with typed, validated transitions."""

from __future__ import annotations

from enum import Enum


class JobState(str, Enum):
    CREATED = "CREATED"
    UPLOAD_PENDING = "UPLOAD_PENDING"
    UPLOADED = "UPLOADED"
    INPUT_VALIDATION = "INPUT_VALIDATION"
    CAPTURE_QUALITY_ANALYSIS = "CAPTURE_QUALITY_ANALYSIS"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    QUEUED = "QUEUED"
    CAMERA_POSE_ESTIMATION = "CAMERA_POSE_ESTIMATION"
    SPARSE_RECONSTRUCTION = "SPARSE_RECONSTRUCTION"
    DENSE_RECONSTRUCTION = "DENSE_RECONSTRUCTION"
    MESH_OR_SPLAT_GENERATION = "MESH_OR_SPLAT_GENERATION"
    TEXTURE_OR_APPEARANCE_PROCESSING = "TEXTURE_OR_APPEARANCE_PROCESSING"
    ARTIFACT_OPTIMIZATION = "ARTIFACT_OPTIMIZATION"
    ARTIFACT_VALIDATION = "ARTIFACT_VALIDATION"
    PREVIEW_GENERATION = "PREVIEW_GENERATION"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


TERMINAL = {JobState.SUCCEEDED, JobState.PARTIAL_SUCCESS, JobState.FAILED, JobState.CANCELLED, JobState.EXPIRED}

PROCESSING = [
    JobState.INPUT_VALIDATION,
    JobState.CAMERA_POSE_ESTIMATION,
    JobState.SPARSE_RECONSTRUCTION,
    JobState.DENSE_RECONSTRUCTION,
    JobState.MESH_OR_SPLAT_GENERATION,
    JobState.TEXTURE_OR_APPEARANCE_PROCESSING,
    JobState.ARTIFACT_OPTIMIZATION,
    JobState.PREVIEW_GENERATION,
    JobState.ARTIFACT_VALIDATION,
]
ACTIVE = {JobState.CREATED, JobState.QUEUED, JobState.WAITING_FOR_CONFIRMATION, JobState.CANCEL_REQUESTED, *PROCESSING}


def _build() -> dict[JobState, set[JobState]]:
    t: dict[JobState, set[JobState]] = {s: set() for s in JobState}
    t[JobState.CREATED] = {JobState.UPLOAD_PENDING, JobState.QUEUED, JobState.WAITING_FOR_CONFIRMATION,
                           JobState.CANCELLED, JobState.FAILED}
    t[JobState.UPLOAD_PENDING] = {JobState.UPLOADED, JobState.CANCELLED, JobState.EXPIRED}
    t[JobState.UPLOADED] = {JobState.CAPTURE_QUALITY_ANALYSIS, JobState.QUEUED, JobState.CANCELLED}
    t[JobState.CAPTURE_QUALITY_ANALYSIS] = {JobState.WAITING_FOR_CONFIRMATION, JobState.QUEUED, JobState.FAILED}
    t[JobState.WAITING_FOR_CONFIRMATION] = {JobState.QUEUED, JobState.CANCELLED, JobState.EXPIRED}
    t[JobState.QUEUED] = {JobState.INPUT_VALIDATION, JobState.CANCELLED, JobState.CANCEL_REQUESTED, JobState.FAILED}
    # Processing stages may move forward to any later processing stage (stages can be skipped),
    # back to QUEUED for a retry attempt, or to a terminal / cancel state.
    for i, s in enumerate(PROCESSING):
        t[s] = set(PROCESSING[i + 1:]) | {JobState.QUEUED, JobState.SUCCEEDED, JobState.PARTIAL_SUCCESS,
                                          JobState.FAILED, JobState.CANCEL_REQUESTED, JobState.CANCELLED}
    t[JobState.CANCEL_REQUESTED] = {JobState.CANCELLED, JobState.FAILED, JobState.SUCCEEDED,
                                    JobState.PARTIAL_SUCCESS}
    # Terminal states only allow an explicit admin/user retry which creates a new attempt.
    t[JobState.FAILED] = {JobState.QUEUED}
    t[JobState.CANCELLED] = {JobState.QUEUED}
    t[JobState.PARTIAL_SUCCESS] = {JobState.QUEUED}
    return t


TRANSITIONS = _build()


class InvalidTransition(ValueError):
    pass


def check_transition(current: str, new: str) -> JobState:
    cur, nxt = JobState(current), JobState(new)
    if nxt not in TRANSITIONS[cur]:
        raise InvalidTransition(f"{cur.value} -> {nxt.value} is not allowed")
    return nxt


def is_terminal(state: str) -> bool:
    return JobState(state) in TERMINAL
