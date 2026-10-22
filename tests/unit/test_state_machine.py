import pytest
from roomscape_api.state_machine import InvalidTransition, JobState, check_transition, is_terminal


def test_happy_path_transitions():
    seq = ["CREATED", "QUEUED", "INPUT_VALIDATION", "CAMERA_POSE_ESTIMATION", "SPARSE_RECONSTRUCTION",
           "ARTIFACT_OPTIMIZATION", "PREVIEW_GENERATION", "ARTIFACT_VALIDATION", "SUCCEEDED"]
    for a, b in zip(seq, seq[1:]):
        check_transition(a, b)


@pytest.mark.parametrize("a,b", [("SUCCEEDED", "INPUT_VALIDATION"), ("CREATED", "SUCCEEDED"),
                                 ("SPARSE_RECONSTRUCTION", "CAMERA_POSE_ESTIMATION"), ("QUEUED", "SUCCEEDED"),
                                 ("CANCELLED", "SUCCEEDED")])
def test_invalid_transitions_rejected(a, b):
    with pytest.raises(InvalidTransition):
        check_transition(a, b)


def test_retry_and_cancel_paths():
    check_transition("SPARSE_RECONSTRUCTION", "QUEUED")  # retry attempt
    check_transition("SPARSE_RECONSTRUCTION", "CANCEL_REQUESTED")
    check_transition("CANCEL_REQUESTED", "CANCELLED")
    check_transition("FAILED", "QUEUED")  # explicit retry creates a new attempt
    assert is_terminal("PARTIAL_SUCCESS") and not is_terminal(JobState.QUEUED.value)
