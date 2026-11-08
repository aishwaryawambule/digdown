"""Measures diagnosis quality against failures with known causes, so a prompt change
or a new model is judged on results rather than on one run that happened to look good.

    python -m digdown.evaluate --model qwen3:14b --runs 2
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from .config import Config
from .events import EventBus
from .llm import make_llm
from .models import ContainerState, Incident, Signal, SignalType
from .pipeline import Pipeline
from .redactor import Redactor
from .store import Store
from .tools import DiagnosticTools

DEFAULT_CASES = Path(__file__).resolve().parent.parent / "evals" / "cases.yml"


class CaseSource:
    """Stands in for Docker with a recorded failure."""

    def __init__(self, case: dict[str, Any]):
        self.states = [ContainerState(id=c["name"], **c) for c in case["containers"]]
        self.logs = case.get("logs") or {}

    def list_states(self) -> list[ContainerState]:
        return self.states

    def get_logs(self, name: str, lines: int = 200, since_seconds: Optional[int] = None) -> str:
        return self.logs.get(name, "")

    def inspect(self, name: str) -> dict[str, Any]:
        for state in self.states:
            if state.name == name:
                return {"image": state.image, "command": "", "restart_policy": state.restart_policy, "env": {}}
        return {}


def score_diagnosis(case: dict[str, Any], text: str) -> dict[str, bool]:
    lowered = (text or "").lower()
    found = any(phrase.lower() in lowered for phrase in case["cause"])
    mentions = case.get("must_mention")
    if mentions:
        found = found and any(phrase.lower() in lowered for phrase in mentions)
    wrong = any(phrase.lower() in lowered for phrase in case.get("wrong") or [])
    return {"cause_right": found and not wrong}


def score_plan(case: dict[str, Any], kind: Optional[str], actions: list[str]) -> bool:
    wanted = case.get("fix")
    if wanted == "manual":
        return kind == "manual"
    return kind == "automated" and set(wanted or []) <= set(actions)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results) or 1
    return {
        "runs": len(results),
        "cause_right": sum(r["cause_right"] for r in results),
        "accepted_when_right": sum(r["cause_right"] and r["accepted"] for r in results),
        "fix_right": sum(r["fix_right"] for r in results),
        "average_seconds": round(sum(r["seconds"] for r in results) / total),
    }


def run_case(case: dict[str, Any], llm, data_dir: str) -> dict[str, Any]:
    store = Store(str(Path(data_dir) / f"{time.time_ns()}.db"))
    config = Config(mode="execute", reuse_recurring_diagnosis=False)
    source = CaseSource(case)
    pipeline = Pipeline(store, EventBus(), config, llm,
                        DiagnosticTools(source, None, redactor=Redactor()), redactor=Redactor())
    signal = Signal(source="docker", type=SignalType(case["signal"]), target=case["target"],
                    detail=case.get("detail") or {})
    incident = store.add_incident(Incident(source="docker", target=case["target"], signal=signal,
                                           dedup_key=signal.dedup_key))
    started = time.time()
    error = ""
    try:
        pipeline.run(incident)
    except Exception as e:  # noqa: BLE001 - a failed run is a result to report
        error = str(e)[:200]
    diagnosis = store.latest_diagnosis_for(incident.id)
    plan = store.latest_plan_for(incident.id)
    text = f"{diagnosis.summary} {diagnosis.root_cause}" if diagnosis else ""
    actions = [a.type.value for a in plan.actions] if plan else []
    return {
        "case": case["name"], **score_diagnosis(case, text),
        "accepted": bool(diagnosis and diagnosis.critic_supported),
        "fix_right": score_plan(case, plan.kind if plan else None, actions),
        "seconds": round(time.time() - started), "said": text[:220], "actions": actions, "error": error,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--api-key", default="not-needed")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    args = parser.parse_args(argv)
    cases = yaml.safe_load(Path(args.cases).read_text())
    llm = make_llm(args.provider, base_url=args.base_url, api_key=args.api_key, model=args.model)
    results = []
    with tempfile.TemporaryDirectory() as data_dir:
        for _ in range(args.runs):
            for case in cases:
                result = run_case(case, llm, data_dir)
                results.append(result)
                print(json.dumps(result), flush=True)
    print(json.dumps({"model": args.model, **summarize(results)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
