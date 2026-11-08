"""The diagnosis pipeline: drive one incident from DETECTED through triage,
investigation, and the Diagnostician↔Critic loop, persisting a Diagnosis and an
inspectable reasoning trace.

In Observe mode this is the whole job — diagnose and record, no proposal, no
writes. A confirmed diagnosis ends the incident as DIAGNOSED, or moves on to the
Planner in the modes that propose fixes; a diagnosis the Critic rejects after `max_critic_rounds`
ends the incident CLOSED_NO_FIX.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .agent_models import Diagnosis, RootCause
from .agents import Critic, Diagnostician, PlanCritic, Planner
from .allowlist import HOST_TARGET, plan_risk, validate_plan
from .config import Config
from .heal import AUTO_HEAL_IDENTITY, Decision, decide, needed_by_a_failing_app
from .llm import friendly_diagnosis_error
from .models import ContainerState, Incident, IncidentState, SignalType, utcnow
from .notifier import Notifier
from .plan_models import Action, ActionType, Approval, Plan, PlanProposal, RiskLevel
from .redactor import Redactor

# Modes in which Digdown proposes a fix (observe only diagnoses).
_PROPOSING_MODES = {"propose", "execute"}
# Docker reports the facts, but an app can have a second problem too, which the check
# after the fix would catch.
_FROM_DOCKER_CONFIDENCE = 0.9
_CAN_FAIL_ON_A_DEPENDENCY = {SignalType.RESTART_LOOP, SignalType.CRASH, SignalType.UNHEALTHY}
_WORTH_MENTIONING = timedelta(hours=1)

# Fixes applied to the container directly rather than through Compose.
_ACTS_ON_CONTAINER = {ActionType.RESTART, ActionType.START, ActionType.SET_RESOURCE_LIMIT}

# How much of the failing container's log goes straight to the model. Enough for a
# stack trace, without crowding a small local model's memory.
_FACT_LOG_LINES = 40
_FACT_LOG_CHARS = 3000

# A reused prior must have been at least this confident.
_REUSE_MIN_CONFIDENCE = 0.5


class Pipeline:
    def __init__(self, store, bus, config: Config, llm, tools, redactor=None,
                 notifier=None, unavailable_actions=frozenset(),
                 compose_project=None):
        self.unavailable_actions = set(unavailable_actions)
        self.compose_project = compose_project
        self.start_fix = None  # set by the API once it can run fixes; auto heal needs it
        self.store = store
        self.bus = bus
        self.config = config
        self.tools = tools
        self.redactor = redactor or Redactor()
        self.notifier = notifier or Notifier()
        self.diagnostician = Diagnostician(llm, tools, config.max_tool_calls)
        self.critic = Critic(llm, tools, config.max_tool_calls)
        self.planner = Planner(llm, tools, config.max_tool_calls)
        self.plan_critic = PlanCritic(llm, tools, config.max_tool_calls)

    # ---- orchestration ----

    def _learn_secrets(self) -> None:
        """Mask secrets from every watched container, not just the one failing: an app
        can log the password of the database it's connecting to. A Compose file isn't
        needed, so this works for every stack, and over SSH."""
        source = self.tools.source
        try:
            for state in source.list_states():
                env = (source.inspect(state.name) or {}).get("env") or {}
                self.redactor.registry.register_secret_settings(env)
        except Exception:  # noqa: BLE001 - the patterns still apply if this can't run
            pass

    def run(self, incident: Incident) -> Optional[Diagnosis]:
        self._learn_secrets()
        # It may have waited a while in the queue. If the problem has gone away
        # since, there's nothing to diagnose and no reason to spend a model call.
        current = self.store.get_incident(incident.id)
        if current is not None and current.state != IncidentState.DETECTED:
            return None

        # Triage is deterministic: the signal already carries the type.
        self._transition(incident, IncidentState.TRIAGED, "triager", "triaged")
        self._transition(incident, IncidentState.INVESTIGATING, "pipeline",
                         "investigating")

        # A stopped dependency is a fact Docker reports, so it needs no AI, and gets
        # fixed in seconds rather than minutes.
        answered = self._answer_from_docker(incident)
        if answered is not None:
            return answered

        # Same issue we've RESOLVED before? Replay that result instead of
        # spending another LLM diagnosis (config: reuse_recurring_diagnosis).
        # Anything that wasn't resolved falls through to a fresh diagnosis.
        if self.config.reuse_recurring_diagnosis:
            cached = self._reuse_prior_diagnosis(incident)
            if cached is not None:
                return cached

        root_cause, rounds, verdict = self._diagnose_with_critic(incident)

        # Defense in depth: redact any secret the model might have echoed before
        # it lands in the store or the dashboard.
        diagnosis = self.store.add_diagnosis(Diagnosis(
            incident_id=incident.id,
            summary=self.redactor.redact(root_cause.summary),
            root_cause=self.redactor.redact(root_cause.root_cause),
            evidence=self.redactor.redact_list(root_cause.evidence),
            confidence=root_cause.confidence,
            rounds=rounds,
            critic_supported=verdict.supported,
            critic_reason=verdict.reason,
        ))
        self.store.append_audit(incident.id, actor="diagnostician", event="diagnosed",
                                payload=diagnosis.model_dump(mode="json"))
        self._save_trace(incident.id)

        if not verdict.supported:
            self._transition(incident, IncidentState.CLOSED_NO_FIX, "critic",
                             "closed_no_fix",
                             payload={"reason": verdict.reason,
                                      "issues": verdict.issues})
            self.notifier.notify(
                "needs_manual", incident,
                f"Digdown couldn't confirm the cause, so it changed nothing. "
                f"Its best guess: {diagnosis.summary} Why it wasn't sure: {diagnosis.critic_reason}")
            return diagnosis

        if self.config.mode in _PROPOSING_MODES:
            self._propose(incident, root_cause)
        else:
            self._finish_explained(incident)
        return diagnosis

    def needs_no_ai(self, incident: Incident) -> bool:
        return stopped_dependency(incident, self._states()) is not None

    def _answer_from_docker(self, incident: Incident) -> Optional[Diagnosis]:
        try:
            states = self.tools.source.list_states()
        except Exception:  # noqa: BLE001 - the AI can still look into it
            return None
        dependency = stopped_dependency(incident, states)
        if dependency is None:
            return None
        apps = [s for s in states if s.name != dependency.name
                and s.project == dependency.project and dependency.service in s.depends_on]
        names = ", ".join(s.name for s in apps) or incident.target
        if incident.signal.type == SignalType.CREATED:
            summary = f"{dependency.name} was created but never started."
            cause = f"{summary} Docker's record doesn't show why."
        else:
            summary = f"{names} can't run because {dependency.name}, which it depends on, is stopped."
            cause = (f"{dependency.name} has stopped, and {names} depends on it. "
                     "Docker's record doesn't show why it stopped.")
        diagnosis = self.store.add_diagnosis(Diagnosis(
            incident_id=incident.id,
            summary=summary,
            root_cause=cause,
            evidence=[f"{dependency.name} is {dependency.status}, exit code {dependency.exit_code}"]
                     + [f"{s.name} is {s.status}, {s.restart_count} restarts" for s in apps],
            confidence=_FROM_DOCKER_CONFIDENCE,
            rounds=0,
            critic_supported=True,
            critic_reason="Read directly from Docker, without using the AI.",
        ))
        self.store.append_audit(incident.id, actor="digdown", event="diagnosed",
                                payload=diagnosis.model_dump(mode="json"))
        if self.config.mode not in _PROPOSING_MODES:
            self._finish_explained(incident)
            return diagnosis
        actions = [Action(type=ActionType.START, target=dependency.name,
                          rationale=summary)]
        self._register_plan(incident, Plan(
            incident_id=incident.id, summary=describe_fix(actions, states), kind="automated",
            risk=plan_risk(actions), actions=actions, critic2_supported=True,
            critic2_reason="A fixed rule, based on what Docker reports."), "digdown")
        return diagnosis

    def _finish_explained(self, incident: Incident) -> None:
        """Explain-only mode: the job is done once the cause is known."""
        self._transition(incident, IncidentState.DIAGNOSED, "pipeline", "explained")

    def _propose(self, incident: Incident, root_cause: RootCause) -> Plan:
        if incident.signal.type == SignalType.DISK_FULL and self.config.auto_heal_disk_cleanup:
            plan = Plan(incident_id=incident.id,
                        summary="Clear Docker's unused images and build cache.",
                        kind="automated", risk=RiskLevel.MEDIUM, critic2_supported=True,
                        critic2_reason="A fixed rule you turned on, rather than the AI's choice.",
                        actions=[Action(type=ActionType.CLEAN_DOCKER, target=HOST_TARGET)])
            return self._register_plan(incident, plan, "auto heal")
        plan = self._plan_with_critic(incident, root_cause)
        return self._register_plan(incident, plan, "planner")

    def _register_plan(self, incident: Incident, plan: Plan, actor: str) -> Plan:
        """Persist a plan and move the incident to PROPOSED (automated + accepted)
        or CLOSED_NO_FIX (manual / unsupported), notifying either way. Shared by a
        freshly-planned fix and a replayed one (so reuse goes through the same
        approval gate)."""
        self.store.add_plan(plan)
        if plan.kind == "automated" and plan.critic2_supported:
            # Before it shows as waiting: reading Docker takes seconds, and a click in
            # that gap plus auto heal would run the fix twice.
            decision = self._auto_heal_decision(incident, plan)
            self._transition(incident, IncidentState.PROPOSED, actor,
                             "proposed", payload=plan.model_dump(mode="json"))
            if decision.allowed:
                self._auto_approve(incident, plan)
                return plan
            detail = plan.summary
            if decision.reason:
                self.store.append_audit(incident.id, actor=AUTO_HEAL_IDENTITY,
                                        event="auto_heal_skipped",
                                        payload={"reason": decision.reason})
                detail += f" Auto heal didn't run: {decision.reason}"
            if not decision.quiet:
                self.notifier.notify("needs_approval", incident, detail)
        else:
            # Propose-and-explain: a diagnosis + recommended manual fix, no action.
            self._transition(incident, IncidentState.CLOSED_NO_FIX, actor,
                             "closed_no_fix",
                             payload={"manual_fix": plan.manual_fix,
                                      "reason": plan.critic2_reason})
            self.notifier.notify("needs_manual", incident, plan.manual_fix)
        return plan

    def _auto_heal_decision(self, incident: Incident, plan: Plan) -> Decision:
        disk = incident.signal.type == SignalType.DISK_FULL and self.config.auto_heal_disk_cleanup
        if self.start_fix is None or not (self.config.auto_heal_stacks or disk):
            return Decision(False)
        source = self.tools.source
        try:
            states = source.list_states()
        except Exception:  # noqa: BLE001 - can't see the container, so a person decides
            return Decision(False, "Couldn't check the container's current state.")
        return decide(incident, plan, self.store.latest_diagnosis_for(incident.id),
                      config=self.config, states=states, store=self.store,
                      stopped_on_purpose=getattr(source, "stopped_on_purpose", lambda _t: True))

    def retry_auto_heal(self, incident: Incident) -> None:
        """A fix was already waiting when auto heal was allowed to act on it. If the
        rules still say no, it carries on waiting for a person, as before."""
        plan = self.store.latest_plan_for(incident.id)
        if plan is None or plan.kind != "automated" or not plan.critic2_supported:
            return
        if self._auto_heal_decision(incident, plan).allowed:
            self._auto_approve(incident, plan)

    def _auto_approve(self, incident: Incident, plan: Plan) -> None:
        self.store.add_approval(Approval(incident_id=incident.id, decision="approve",
                                         identity=AUTO_HEAL_IDENTITY))
        self._transition(incident, IncidentState.APPROVED, AUTO_HEAL_IDENTITY, "auto_approved",
                         payload={"summary": plan.summary})
        self.start_fix(incident)

    def _reuse_prior_diagnosis(self, incident: Incident) -> Optional[Diagnosis]:
        """If this same problem (same error_signature) was diagnosed on a recent
        earlier incident with a confident, Critic-supported result, confirm it's
        still happening now (plain-code, no LLM) and replay that diagnosis — and
        its fix — instead of calling the LLM again. A RESOLVED prior that recurs
        is additionally flagged as a regression. Returns the reused Diagnosis, or
        None (the caller then diagnoses fresh)."""
        since = (utcnow() - timedelta(
            hours=self.config.reuse_recurrence_window_hours)).isoformat()
        prior_id = self.store.latest_diagnosed_incident_for_signature(
            incident.error_signature, incident.id, since)
        if prior_id is None:
            return None
        prior_inc = self.store.get_incident(prior_id)
        prior = self.store.latest_diagnosis_for(prior_id)
        if (prior_inc is None or prior is None
                or not prior.critic_supported
                or prior.confidence < _REUSE_MIN_CONFIDENCE):
            return None
        if not self._confirm_recurrence(incident, prior_inc):
            return None  # diverged / unverifiable → diagnose fresh

        # Link this occurrence to the one we're reusing.
        self.store.set_parent_incident(incident.id, prior_id)
        incident.parent_incident_id = prior_id

        regression = prior_inc.state == IncidentState.RESOLVED
        if regression:
            self.store.set_recurred_after_fix(incident.id, True)
            incident.recurred_after_fix = True

        diagnosis = self.store.add_diagnosis(Diagnosis(
            incident_id=incident.id,
            summary=prior.summary,
            root_cause=prior.root_cause,
            evidence=prior.evidence,
            confidence=prior.confidence,
            rounds=0,                      # replayed — no diagnose↔critic rounds
            critic_supported=prior.critic_supported,
            critic_reason=prior.critic_reason,
        ))
        self.store.append_audit(
            incident.id, actor="pipeline", event="diagnosed_from_cache",
            payload={"reused_from_incident_id": prior_id,
                     "recurred_after_fix": regression,
                     "diagnosis": diagnosis.model_dump(mode="json")})
        self._publish(incident)
        if regression:
            self.notifier.notify(
                "regression", incident,
                f"Recurred after a fix (reused diagnosis from #{prior_id}).")

        # Replay the prior fix too (skip the Planner LLM) when proposing and the
        # earlier incident produced a plan. Routed through the normal gate.
        prior_plan = self.store.latest_plan_for(prior_id)
        if self.config.mode not in _PROPOSING_MODES:
            self._finish_explained(incident)
        elif prior_plan is not None and not self._cannot_run(prior_plan.actions):
            replay = prior_plan.model_copy(update={
                "id": None, "incident_id": incident.id, "created_at": utcnow()})
            self._register_plan(incident, replay, "cache")
        else:
            self._propose(incident, RootCause(
                summary=diagnosis.summary, root_cause=diagnosis.root_cause,
                evidence=diagnosis.evidence, confidence=diagnosis.confidence))
        return diagnosis

    def _cannot_run(self, actions) -> list[str]:
        return [f"{a.type.value} isn't available for {a.target} on this install. Choose "
                "another action, or give a manual fix."
                for a in actions
                if a.type in self.unavailable_actions or self._outside_compose_file(a)]

    def _outside_compose_file(self, action) -> bool:
        """These actions run `docker compose -f <file> up <service>`, which acts on
        that file's stack whichever container the plan meant. A service with the same
        name in another stack would get the wrong container recreated."""
        if self.compose_project is None or action.type not in (
                ActionType.RECREATE, ActionType.SET_ENV_VAR):
            return False
        project = self.tools.project
        for state in self.tools.source.list_states():
            if state.name == action.target:
                return state.project != self.compose_project or state.service not in project.services
        return True  # a bare service name can't be tied to one stack

    def _container_facts(self, incident: Incident) -> str:
        """Facts read from Docker. Without them a model has to guess the current limit,
        and with nothing to base a new one on, it tends to hand the fix back to a person."""
        try:
            states = self.tools.source.list_states()
        except Exception:  # noqa: BLE001 - the plan can still go ahead without them
            return ""
        state = next((s for s in states if s.name == incident.target), None)
        if state is None:
            return ""
        lines = [f"Container now: status={state.status}, exit_code={state.exit_code}, "
                 f"out_of_memory={state.oom_killed}, restarts={state.restart_count}."]
        if state.mem_limit_bytes:
            lines.append(f"Its memory limit is {state.mem_limit_bytes} bytes. Double that "
                         f"is {state.mem_limit_bytes * 2} bytes.")
        else:
            lines.append("It has no memory limit, so set_resource_limit doesn't apply.")
        for service in state.depends_on:
            for dep in states:
                if dep.service == service and dep.project == state.project:
                    health = f", {dep.health.value}" if dep.health.value != "none" else ""
                    lines.append(f"It depends on {dep.name}, which is {dep.status}"
                                 f"{health}, exit_code={dep.exit_code}.")
        for other in states:
            if (other.name != state.name and other.project == state.project
                    and state.service in other.depends_on):
                lines.append(f"{other.name} depends on this container, and is {other.status}, "
                             f"exit_code={other.exit_code}, restarts={other.restart_count}.")
        lines.append(self._own_log(incident.target))
        return self.redactor.redact("\n".join(lines))

    def _own_log(self, target: str) -> str:
        """The end of the failing container's log, labelled as untrusted and masked by
        the tool. Having it up front is faster than asking for it, and stops a model
        from settling on an event it noticed in some other container's log."""
        try:
            log = self.tools.get_recent_logs(target, lines=_FACT_LOG_LINES)
        except Exception:  # noqa: BLE001 - the model can still use its tools
            return ""
        if len(log) <= _FACT_LOG_CHARS:
            return f"The end of its own log:\n{log}"
        return (f"The end of its own log, with the start cut off:\n<untrusted-logs target={target}>\n"
                f"{log[-_FACT_LOG_CHARS:]}")

    def _pin_to_container(self, incident: Incident, actions) -> list:
        """The AI may name a fix's target `db`, and other stacks may have one too. The
        one meant is in the failing container's stack, so record exactly that container.
        Recreating goes through Compose, which needs the service name, and is already
        limited to the Compose file's own stack."""
        try:
            states = self.tools.source.list_states()
        except Exception:  # noqa: BLE001 - left as named; running it refuses if ambiguous
            return list(actions)
        names = {s.name for s in states}
        stack = next((s.project for s in states if s.name == incident.target), None)
        pinned = []
        for action in actions:
            matches = [s.name for s in states if s.service == action.target and s.project == stack]
            if (action.type in _ACTS_ON_CONTAINER and action.target not in names
                    and stack is not None and len(matches) == 1):
                action = action.model_copy(update={"target": matches[0]})
            pinned.append(action)
        return pinned

    def _plan_with_critic(self, incident: Incident, root_cause: RootCause) -> Plan:
        valid = self.tools.valid_targets()
        facts = self._container_facts(incident)
        feedback = None
        last_proposal = PlanProposal(summary="no plan")
        for _ in range(self.config.max_critic_rounds):
            proposal = self.planner.plan(incident, root_cause, valid, feedback, facts=facts)
            last_proposal = proposal
            if not proposal.actions:
                return self._manual_plan(incident, proposal.manual_fix or
                                         "No safe automated fix is available.")
            errors = (validate_plan(proposal.actions, valid) + self._cannot_run(proposal.actions)
                      + [f"{a.type.value} isn't available" for a in proposal.actions
                         if a.type == ActionType.CLEAN_DOCKER])
            if errors:
                feedback = "These actions are invalid; fix them:\n" + "\n".join(errors)
                continue
            verdict = self.plan_critic.review(incident, root_cause, proposal, facts=facts)
            if verdict.supported:
                actions = [a.model_copy(update={
                    "rationale": self.redactor.redact(a.rationale)})
                    for a in self._pin_to_container(incident, proposal.actions)]
                return Plan(
                    incident_id=incident.id,
                    summary=self.redactor.redact(describe_fix(proposal.actions, self._states())),
                    kind="automated",
                    risk=plan_risk(proposal.actions),
                    actions=actions,
                    critic2_supported=True,
                    critic2_reason=self.redactor.redact(verdict.reason),
                )
            feedback = "; ".join([verdict.reason, *verdict.issues]).strip("; ")
        # Exhausted retries without an accepted automated plan.
        return self._manual_plan(
            incident, last_proposal.manual_fix or
            "No safe automated fix could be validated; manual intervention needed.")

    def _states(self) -> list:
        try:
            return self.tools.source.list_states()
        except Exception:  # noqa: BLE001 - only used for extra detail
            return []

    def _manual_plan(self, incident: Incident, manual_fix: str) -> Plan:
        return Plan(incident_id=incident.id, summary="Manual fix recommended",
                    kind="manual", risk=RiskLevel.LOW,
                    manual_fix=self.redactor.redact(manual_fix),
                    critic2_supported=False)

    def _step_publisher(self, incident):
        def publish(step):
            clean = dict(step)
            content = clean.get("content")
            if content:
                clean["content"] = self.redactor.redact(content)
            tcs = clean.get("tool_calls")
            if tcs:
                clean["tool_calls"] = [
                    {**tc, "function": {**tc.get("function", {}),
                                        "arguments": self.redactor.redact(
                                            tc.get("function", {}).get("arguments", ""))}}
                    for tc in tcs
                ]
            self.bus.publish({"type": "reasoning_step",
                              "incident_id": incident.id, "step": clean})
        return publish

    def _diagnose_with_critic(self, incident: Incident):
        feedback: Optional[str] = None
        root_cause = verdict = None
        rounds = self.config.max_critic_rounds
        for r in range(1, self.config.max_critic_rounds + 1):
            facts = self._container_facts(incident)
            root_cause = self.diagnostician.diagnose(
                incident, feedback, on_step=self._step_publisher(incident), facts=facts)
            verdict = self.critic.review(incident, root_cause, facts=facts)
            if verdict.supported:
                return root_cause, r, verdict
            feedback = "; ".join([verdict.reason, *verdict.issues]).strip("; ")
        return root_cause, rounds, verdict

    def _confirm_recurrence(self, incident: Incident, prior_inc: Incident) -> bool:
        """Only reuse an earlier answer whose fix worked. Anything else gets diagnosed
        fresh."""
        return prior_inc.state == IncidentState.RESOLVED

    # ---- helpers ----

    def mark_diagnosis_failed(self, incident, exc) -> None:
        """A diagnosis attempt raised: move the incident out of INVESTIGATING into
        DIAGNOSIS_FAILED, record a human-readable reason (raw error kept in the
        audit payload), and broadcast so the dashboard updates live."""
        reason = friendly_diagnosis_error(exc)
        self.store.set_failure_reason(incident.id, reason)
        incident.failure_reason = reason
        self._transition(incident, IncidentState.DIAGNOSIS_FAILED, "pipeline",
                         "diagnosis_failed",
                         payload={"error": str(exc), "reason": reason})

    def _transition(self, incident, state, actor, event, payload=None):
        self.store.update_state(incident.id, state)
        incident.state = state
        self.store.append_audit(incident.id, actor=actor, event=event,
                                payload=payload or {})
        self._publish(incident)

    def _publish(self, incident):
        self.bus.publish({"type": "incident_updated",
                          "incident": incident.model_dump(mode="json")})

    def _save_trace(self, incident_id: int):
        result = self.diagnostician.last_result
        if result is None:
            return
        # Persist the reasoning steps (sans the static system prompt) so the
        # dashboard can show how the conclusion was reached.
        trace = [m for m in result.transcript if m.get("role") != "system"]
        self.store.append_audit(incident_id, actor="diagnostician", event="reasoning",
                                payload={"steps": trace,
                                         "tool_calls": result.tool_calls_made})


def stopped_dependency(incident: Incident, states: list[ContainerState]) -> Optional[ContainerState]:
    """The container to start, when Docker alone shows the cause: an app is failing and
    something it depends on stopped cleanly, or a container never started. Something
    that crashed isn't covered, as starting it may just crash again, so the reason
    matters and the AI looks into it."""
    target = next((s for s in states if s.name == incident.target), None)
    if target is None:
        return None
    if incident.signal.type == SignalType.CREATED:
        return target if target.status == "created" else None
    if incident.signal.type == SignalType.STOPPED:
        needed = _stopped_cleanly(target) and needed_by_a_failing_app(target, states)
        return target if needed else None
    if incident.signal.type not in _CAN_FAIL_ON_A_DEPENDENCY:
        return None
    for service in target.depends_on:
        for dep in states:
            if dep.service == service and dep.project == target.project and _stopped_cleanly(dep):
                return dep
    return None


def _stopped_cleanly(state: ContainerState) -> bool:
    return (state.status in ("exited", "dead") and not state.oom_killed
            and (state.exit_code or 0) == 0)


def describe_fix(actions: list[Action], states: list[ContainerState] = (),
                 now: Optional[datetime] = None) -> str:
    """What a fix does, from what it actually does. The AI's own summary can say
    "restart" when the action is "start". Never includes a setting's value."""
    parts = []
    done = set()
    for index, action in enumerate(actions):
        if index in done:
            continue
        target = action.target
        if action.type == ActionType.SET_RESOURCE_LIMIT:
            size = _size(action.params.get("mem_limit_bytes", 0))
            then_restart = next((i for i, later in enumerate(actions) if i > index
                                 and later.type == ActionType.RESTART and later.target == target), None)
            if then_restart is not None:
                done.add(then_restart)
                parts.append(f"Raise the {target} memory limit to {size} and restart it.")
            else:
                parts.append(f"Raise the {target} memory limit to {size}.")
        elif action.type == ActionType.START:
            parts.append(f"Start {target}{_down_for(target, states, now)}.")
        elif action.type == ActionType.SET_ENV_VAR:
            parts.append(f"Change the setting {action.params.get('key')} on {target}.")
        elif action.type == ActionType.CLEAN_DOCKER:
            parts.append("Clear Docker's unused images and build cache.")
        else:
            parts.append(f"{action.type.value.capitalize()} {target}.")
    return " ".join(parts)


def _size(size: int) -> str:
    megabytes = size / (1024 * 1024)
    if megabytes >= 1024 and megabytes % 1024 == 0:
        return f"{int(megabytes // 1024)} GB"
    return f"{round(megabytes)} MB"


def _down_for(target: str, states: list[ContainerState], now: Optional[datetime]) -> str:
    """Bringing back something long stopped is the fix most likely to surprise someone,
    so they should be able to see that's what happened."""
    state = next((s for s in states if s.name == target), None)
    if state is None or state.finished_at is None or state.status == "running":
        return ""
    down = (now or utcnow()) - state.finished_at
    if down < _WORTH_MENTIONING:
        return ""
    amount, unit = (down.days, "day") if down.days else (down.seconds // 3600, "hour")
    return f", which had been down for {amount} {unit}{'' if amount == 1 else 's'}"


def recover_interrupted(store, config: Config) -> None:
    """Called at startup. Nothing is running yet, so anything recorded as in progress
    was interrupted by a restart and would otherwise stay in that state forever."""
    for incident in store.incidents_in_states(
            [IncidentState.TRIAGED, IncidentState.INVESTIGATING]):
        # With the cause known, explain-only mode is finished. Otherwise it still
        # needs a fix planned, so it goes round again rather than stopping short.
        finished = (config.mode not in _PROPOSING_MODES
                    and store.latest_diagnosis_for(incident.id) is not None)
        store.update_state(incident.id,
                           IncidentState.DIAGNOSED if finished else IncidentState.DETECTED)
    if config.mode in _PROPOSING_MODES:
        # Explained while fixes weren't allowed, which they are now.
        for incident in store.incidents_in_state(IncidentState.DIAGNOSED):
            store.update_state(incident.id, IncidentState.DETECTED)
    if config.mode == "execute":
        # Approved but not started, so nothing has changed. Ask again.
        for incident in store.incidents_in_state(IncidentState.APPROVED):
            store.update_state(incident.id, IncidentState.PROPOSED)
    for incident in store.incidents_in_states(
            [IncidentState.EXECUTING, IncidentState.VERIFYING]):
        store.set_failure_reason(
            incident.id, "Digdown restarted while applying this fix, so it may be "
                         "only partly applied. Check the container.")
        store.update_state(incident.id, IncidentState.FAILED)
        store.append_audit(incident.id, actor="digdown", event="interrupted_by_restart",
                           payload={})

