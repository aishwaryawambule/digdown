from digdown.executor_models import Execution, ExecResult, StepResult


def test_step_result_defaults():
    s = StepResult(type="restart", target="web", ok=True)
    assert s.detail == ""
    assert s.rolled_back is False


def test_execution_roundtrips():
    e = Execution(
        incident_id=4,
        result=ExecResult.ROLLED_BACK,
        verified=False,
        rolled_back=True,
        steps=[StepResult(type="set_env_var", target="web", ok=True, rolled_back=True)],
        snapshot={"web": {"image": "web@sha256:abc"}},
        detail="verification failed: still unhealthy",
    )
    dumped = e.model_dump(mode="json")
    assert dumped["result"] == "rolled_back"
    assert dumped["steps"][0]["rolled_back"] is True
    back = Execution.model_validate(dumped)
    assert back.snapshot["web"]["image"] == "web@sha256:abc"
