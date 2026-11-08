"""The scoring has to be right, or the numbers it produces mean nothing."""
import yaml

from digdown.evaluate import DEFAULT_CASES, CaseSource, run_case, score_diagnosis, score_plan, summarize
from digdown.llm import FakeLLM, LLMResponse

CASE = {"cause": ["typo"], "wrong": ["memory"]}


def test_the_right_cause_passes():
    assert score_diagnosis(CASE, "A TYPO in the config")["cause_right"]


def test_a_missing_cause_fails():
    assert not score_diagnosis(CASE, "It crashed")["cause_right"]


def test_a_right_phrase_alongside_a_wrong_one_fails():
    assert not score_diagnosis(CASE, "a typo, then running out of memory")["cause_right"]


def test_a_required_mention_is_enforced():
    case = {"cause": ["stopped"], "must_mention": ["db"]}
    assert not score_diagnosis(case, "It stopped")["cause_right"]
    assert score_diagnosis(case, "The db stopped")["cause_right"]


def test_a_person_fix_passes_only_when_nothing_automatic_is_planned():
    assert score_plan({"fix": "manual"}, "manual", [])
    assert not score_plan({"fix": "manual"}, "automated", ["restart"])


def test_an_automatic_fix_needs_the_expected_action():
    assert score_plan({"fix": ["start"]}, "automated", ["start"])
    assert not score_plan({"fix": ["start"]}, "automated", ["restart"])
    assert not score_plan({"fix": ["start"]}, None, [])


def test_summary_counts_results():
    rows = [{"cause_right": True, "accepted": True, "fix_right": False, "seconds": 10},
            {"cause_right": True, "accepted": False, "fix_right": True, "seconds": 30}]
    assert summarize(rows) == {"runs": 2, "cause_right": 2, "accepted_when_right": 1,
                               "fix_right": 1, "average_seconds": 20}


def test_every_case_file_entry_loads_as_real_containers():
    cases = yaml.safe_load(DEFAULT_CASES.read_text())
    assert len(cases) >= 6
    for case in cases:
        names = [s.name for s in CaseSource(case).list_states()]
        assert case["target"] in names and case["cause"] and "fix" in case


def test_a_whole_case_runs_and_is_scored(tmp_path):
    case = yaml.safe_load(DEFAULT_CASES.read_text())[3]  # missing secret
    answers = ['{"summary": "Missing STRIPE_WEBHOOK_SECRET", "root_cause": "KeyError", "confidence": 0.9}',
               '{"supported": true, "reason": "fits"}',
               '{"summary": "Add it", "actions": [], "manual_fix": "Set the secret"}']
    result = run_case(case, FakeLLM([LLMResponse(content=a) for a in answers]), str(tmp_path))
    assert result["cause_right"] and result["accepted"] and result["fix_right"]
