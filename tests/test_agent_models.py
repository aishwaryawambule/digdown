import pytest
from pydantic import ValidationError

from digdown.agent_models import RootCause, CriticVerdict, Diagnosis


def test_root_cause_defaults_and_validation():
    rc = RootCause(summary="web crashed", root_cause="bad env var DB_HOST")
    assert rc.evidence == []
    assert 0.0 <= rc.confidence <= 1.0
    # confidence is clamped to [0, 1]
    with pytest.raises(ValidationError):
        RootCause(summary="x", root_cause="y", confidence=1.5)


def test_critic_verdict_defaults():
    v = CriticVerdict(supported=True, reason="evidence is solid")
    assert v.issues == []
    assert v.supported is True


def test_diagnosis_roundtrips():
    d = Diagnosis(
        incident_id=3,
        summary="OOM under load",
        root_cause="memory limit too low",
        evidence=["exit 137", "OOMKilled=true"],
        confidence=0.8,
        rounds=2,
        critic_supported=True,
        critic_reason="evidence supports the conclusion",
    )
    dumped = d.model_dump(mode="json")
    assert dumped["incident_id"] == 3
    assert dumped["evidence"] == ["exit 137", "OOMKilled=true"]
    assert Diagnosis.model_validate(dumped).confidence == 0.8
