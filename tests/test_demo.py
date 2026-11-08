"""The demo must exercise the real pipeline, ending each failure the way it claims,
with no Docker and no model."""
import pytest

from digdown.demo import build_demo
from digdown.models import IncidentState
from digdown.plan_models import Approval


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def demo():
    clock = Clock()
    d = build_demo(clock=clock, llm_delay=0, verify_interval=0)
    d.clock = clock
    yield d
    d.cleanup()


def _open(demo, target, at):
    demo.clock.now = at
    for opened in iter(demo.watcher.poll_once, []):
        for incident in opened:
            if incident.target == target:
                return incident
    raise AssertionError(f"no incident opened for {target}")


def _approve(demo, incident):
    demo.store.add_approval(Approval(incident_id=incident.id, decision="approve"))
    demo.executor.execute(incident)
    return demo.store.get_incident(incident.id)


def test_nothing_is_broken_at_the_start(demo):
    assert demo.watcher.poll_once() == []


def test_history_gives_trends_something_to_show(demo):
    assert len(demo.store.list_incidents()) >= 5


def test_memory_failure_is_diagnosed_fixed_and_verified(demo):
    incident = _open(demo, "shop-cache", at=5)
    demo.pipeline.run(incident)
    assert demo.store.get_incident(incident.id).state == IncidentState.PROPOSED
    assert "out of memory" in demo.store.latest_diagnosis_for(incident.id).summary
    assert len(demo.store.latest_plan_for(incident.id).actions) == 2

    final = _approve(demo, incident)
    assert final.state == IncidentState.RESOLVED
    assert demo.store.latest_execution_for(incident.id).verified is True
    assert demo.source.limit_of("shop-cache") == 512
    assert incident.id in demo.store.verified_incident_ids()


def test_missing_secret_explains_instead_of_guessing(demo):
    incident = _open(demo, "shop-worker", at=35)
    demo.pipeline.run(incident)
    assert demo.store.get_incident(incident.id).state == IncidentState.CLOSED_NO_FIX
    plan = demo.store.latest_plan_for(incident.id)
    assert plan.kind == "manual"
    assert "STRIPE_WEBHOOK_SECRET" in plan.manual_fix


def test_fix_that_does_not_help_is_undone(demo):
    incident = _open(demo, "shop-web", at=61)
    demo.pipeline.run(incident)
    assert demo.store.get_incident(incident.id).state == IncidentState.PROPOSED
    assert _approve(demo, incident).state == IncidentState.ROLLED_BACK


def test_closed_failures_do_not_reopen_while_still_broken(demo):
    incident = _open(demo, "shop-worker", at=35)
    demo.pipeline.run(incident)
    assert [i for i in demo.watcher.poll_once() if i.target == "shop-worker"] == []


def test_the_demo_touches_nothing_real():
    import digdown.demo as module
    assert not hasattr(module, "DockerWriter") and not hasattr(module, "make_llm")


def test_auto_heal_fixes_the_photos_app_with_nobody_clicking(demo):
    incident = _open(demo, "photos-server", at=19)
    started = []
    demo.pipeline.start_fix = started.append
    demo.pipeline.run(incident)
    assert [i.id for i in started] == [incident.id]
    demo.executor.execute(started[0])
    assert demo.store.get_incident(incident.id).state == IncidentState.RESOLVED
    assert incident.id in demo.store.auto_healed_incident_ids()


def test_the_shop_stack_still_waits_for_a_click(demo):
    incident = _open(demo, "shop-cache", at=5)
    demo.pipeline.start_fix = lambda _i: pytest.fail("shop isn't opted in")
    demo.pipeline.run(incident)
    assert demo.store.get_incident(incident.id).state == IncidentState.PROPOSED


def test_history_shows_a_past_auto_heal(demo):
    assert demo.store.auto_healed_incident_ids()


def test_healthy_demo_containers_stay_below_the_memory_warning(demo):
    # Otherwise a stray warning appears for a container that was meant to be fine.
    for second in range(0, 300, 7):
        demo.clock.now = second
        for state in demo.source.list_states():
            if state.mem_percent is not None:
                assert state.mem_percent < 90, (state.name, second)


def test_the_linked_failure_moves_on_after_the_first_is_fixed(demo):
    cache = _open(demo, "shop-cache", at=5)
    demo.pipeline.run(cache)
    worker = _open(demo, "shop-worker", at=35)
    assert worker.parent_incident_id == cache.id  # waits while cache needs a decision
    _approve(demo, cache)
    demo.store._conn.execute("UPDATE incidents SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                             (cache.id,))
    demo.store._conn.commit()
    assert worker.id in [i.id for i in demo.watcher.poll_once()]


def test_the_auto_heal_page_has_something_to_show(demo):
    outcomes = {r["outcome"] for r in demo.store.auto_heal_activity()}
    assert {"fixed", "left_for_you"} <= outcomes


def test_past_auto_heal_activity_shows_when_it_really_happened(demo):
    from datetime import datetime, timedelta, timezone
    rows = demo.store.auto_heal_activity()
    assert all(datetime.fromisoformat(r["ts"]) < datetime.now(timezone.utc) - timedelta(hours=1) for r in rows)
