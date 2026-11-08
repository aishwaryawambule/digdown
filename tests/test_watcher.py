import pytest
from digdown.models import ContainerState, HealthStatus, IncidentState, SignalType
from digdown.watcher import Watcher


def _watcher(store, bus, config, disk=None):
    return Watcher(source=None, store=store, bus=bus, config=config, disk_percent=disk)


def test_detect_crash(store, bus, config):
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="web", status="exited", exit_code=1)]
    sigs = w.detect(states)
    assert [s.type for s in sigs] == [SignalType.CRASH]
    assert sigs[0].target == "web"


def test_detect_oom_when_oomkilled(store, bus, config):
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="web", status="exited", exit_code=137,
                             oom_killed=True)]
    sigs = w.detect(states)
    assert [s.type for s in sigs] == [SignalType.OOM]


def test_detect_unhealthy_running(store, bus, config):
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="db", status="running",
                             health=HealthStatus.UNHEALTHY)]
    sigs = w.detect(states)
    assert [s.type for s in sigs] == [SignalType.UNHEALTHY]


def test_clean_running_container_yields_no_signal(store, bus, config):
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="web", status="running",
                             health=HealthStatus.HEALTHY)]
    assert w.detect(states) == []


def test_detect_stopped_when_should_be_running(store, bus, config):
    # exited cleanly (0) but the restart policy says it should stay up -> STOPPED
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="web", status="exited", exit_code=0,
                             restart_policy="always")]
    sigs = w.detect(states)
    assert [s.type for s in sigs] == [SignalType.STOPPED]
    assert sigs[0].target == "web"


def test_stopped_also_fires_for_unless_stopped_policy(store, bus, config):
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="web", status="exited", exit_code=0,
                             restart_policy="unless-stopped")]
    assert [s.type for s in w.detect(states)] == [SignalType.STOPPED]


def test_stopped_fires_for_service_with_no_restart_policy(store, bus, config):
    # a plain service (restart "no") that stops should still be caught — its name
    # isn't a one-shot pattern, so it's "should be running but isn't".
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="shop-prod", status="exited",
                             exit_code=0, restart_policy="no")]
    assert [s.type for s in w.detect(states)] == [SignalType.STOPPED]


def test_no_stopped_for_oneshot_name(store, bus, config):
    # init/migrate/setup/seed/bootstrap names are one-shot jobs — exempt even at exit 0
    w = _watcher(store, bus, config)
    for name in ("shop-db-init-1", "plane-migrator-1", "minio-setup"):
        states = [ContainerState(id="1", name=name, status="exited", exit_code=0,
                                 restart_policy="no")]
        assert w.detect(states) == [], name


def test_no_stopped_for_on_failure_job(store, bus, config):
    # restart "on-failure" implies a job that may retry — not a long-running service
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="batch-worker", status="exited",
                             exit_code=0, restart_policy="on-failure")]
    assert w.detect(states) == []


def test_detect_paused(store, bus, config):
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="web", status="paused")]
    assert [s.type for s in w.detect(states)] == [SignalType.PAUSED]


def test_created_fires_only_after_it_persists(store, bus, config):
    # a fresh container is briefly 'created' before the daemon starts it — only
    # flag one that is STILL created on a later poll (genuinely failed to start).
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="web", status="created")]
    assert SignalType.CREATED not in [s.type for s in w.detect(states)]  # first sighting
    assert [s.type for s in w.detect(states)] == [SignalType.CREATED]    # persisted


def test_created_clears_when_it_starts(store, bus, config):
    w = _watcher(store, bus, config)
    w.detect([ContainerState(id="1", name="web", status="created")])
    assert w.detect([ContainerState(id="1", name="web", status="running")]) == []
    # slate reset: a single 'created' sighting after running must not fire
    assert SignalType.CREATED not in [s.type for s in
        w.detect([ContainerState(id="1", name="web", status="created")])]


def test_nonzero_exit_is_crash_not_stopped(store, bus, config):
    # a non-zero exit stays CRASH even with a restart policy
    w = _watcher(store, bus, config)
    states = [ContainerState(id="1", name="web", status="exited", exit_code=1,
                             restart_policy="always")]
    assert [s.type for s in w.detect(states)] == [SignalType.CRASH]


def test_restart_loop_after_threshold_increments(store, bus, config):
    # config.restart_loop_threshold == 3
    w = _watcher(store, bus, config)
    # baseline poll establishes restart_count, no signal yet
    assert w.detect([ContainerState(id="1", name="api", status="running",
                                    restart_count=0)]) == []
    # climb to 3 cumulative increments → restart_loop fires
    w.detect([ContainerState(id="1", name="api", status="restarting", restart_count=1)])
    w.detect([ContainerState(id="1", name="api", status="restarting", restart_count=2)])
    sigs = w.detect([ContainerState(id="1", name="api", status="restarting",
                                    restart_count=3)])
    assert SignalType.RESTART_LOOP in [s.type for s in sigs]


def test_disk_full_signal(store, bus, config):
    w = _watcher(store, bus, config, disk=lambda: 95.0)
    sigs = w.detect([])
    assert [s.type for s in sigs] == [SignalType.DISK_FULL]
    assert sigs[0].detail["percent"] == 95.0


def test_poll_once_opens_incident_and_publishes(store, bus, config):
    from tests.conftest import FakeSource
    source = FakeSource([ContainerState(id="1", name="web", status="exited", exit_code=1)])
    w = Watcher(source=source, store=store, bus=bus, config=config)
    opened = w.poll_once()
    assert len(opened) == 1
    assert opened[0].signal.type == SignalType.CRASH
    assert store.get_incident(opened[0].id).state == IncidentState.DETECTED
    assert bus.events[0]["type"] == "incident_created"


def test_poll_once_dedups_open_incident(store, bus, config):
    from tests.conftest import FakeSource
    crash = ContainerState(id="1", name="web", status="exited", exit_code=1)
    source = FakeSource([crash], [crash])  # same crash two polls in a row
    w = Watcher(source=source, store=store, bus=bus, config=config)
    assert len(w.poll_once()) == 1
    assert len(w.poll_once()) == 0           # second poll: dedup, no new incident
    assert len(store.list_incidents()) == 1


def test_poll_once_respects_debounce(store, bus):
    from digdown.config import Config
    from tests.conftest import FakeSource
    cfg = Config(debounce_polls=2)
    crash = ContainerState(id="1", name="web", status="exited", exit_code=1)
    source = FakeSource([crash], [crash])
    w = Watcher(source=source, store=store, bus=bus, config=cfg)
    assert len(w.poll_once()) == 0           # first sighting, below debounce
    assert len(w.poll_once()) == 1           # persisted 2 polls → opens


def test_restart_loop_does_not_fire_on_first_sighting(store, bus, config):
    # A container already restarting before the watcher started must NOT fire
    # restart_loop on first sighting (no observed deltas yet, only a baseline).
    w = Watcher(source=None, store=store, bus=bus, config=config)
    sigs = w.detect([ContainerState(id="1", name="api", status="restarting",
                                    restart_count=7)])
    assert SignalType.RESTART_LOOP not in [s.type for s in sigs]


def test_poll_once_reopens_after_resolution(store, bus, config):
    from tests.conftest import FakeSource
    crash = ContainerState(id="1", name="web", status="exited", exit_code=1)
    source = FakeSource([crash], [crash])
    w = Watcher(source=source, store=store, bus=bus, config=config)
    first = w.poll_once()
    assert len(first) == 1
    store.update_state(first[0].id, IncidentState.RESOLVED)
    reopened = w.poll_once()
    assert len(reopened) == 1
    assert reopened[0].id != first[0].id


def test_poll_once_restart_loop_dedups(store, bus, config):
    from tests.conftest import FakeSource
    frames = [
        [ContainerState(id="1", name="api", status="restarting", restart_count=0)],
        [ContainerState(id="1", name="api", status="restarting", restart_count=1)],
        [ContainerState(id="1", name="api", status="restarting", restart_count=2)],
        [ContainerState(id="1", name="api", status="restarting", restart_count=3)],
        [ContainerState(id="1", name="api", status="restarting", restart_count=3)],
    ]
    source = FakeSource(*frames)
    w = Watcher(source=source, store=store, bus=bus, config=config)
    opened = []
    for _ in range(5):
        opened.extend(w.poll_once())
    restart_incidents = [i for i in opened if i.signal.type == SignalType.RESTART_LOOP]
    assert len(restart_incidents) == 1


def test_restart_loop_does_not_reopen_after_resolution_when_stable(store, bus, config):
    from tests.conftest import FakeSource
    from digdown.models import IncidentState
    restarting = lambda rc: [ContainerState(id="1", name="api", status="restarting",
                                            restart_count=rc)]
    stable = lambda rc: [ContainerState(id="1", name="api", status="running",
                                        restart_count=rc)]
    source = FakeSource(restarting(0), restarting(1), restarting(2), restarting(3),
                        stable(3), stable(3), stable(3), stable(3))
    w = Watcher(source=source, store=store, bus=bus, config=config)
    opened = []
    for _ in range(4):
        opened.extend(w.poll_once())
    rl = [i for i in opened if i.signal.type == SignalType.RESTART_LOOP]
    assert len(rl) == 1
    store.update_state(rl[0].id, IncidentState.RESOLVED)
    reopened = []
    for _ in range(4):
        reopened.extend(w.poll_once())
    assert [i for i in reopened if i.signal.type == SignalType.RESTART_LOOP] == []


def test_correlation_links_dependent_to_open_parent(store, bus, config):
    from digdown.watcher import Watcher
    from digdown.discovery import Project
    from tests.conftest import FakeSource
    # web depends on db; db already has an open incident.
    project = Project(services=["web", "db"], depends_on={"web": ["db"], "db": []})
    db_down = ContainerState(id="2", name="db", status="exited", exit_code=1)
    db_watcher = Watcher(source=FakeSource([db_down]), store=store, bus=bus,
                         config=config, project=project)
    parent = db_watcher.poll_once()[0]
    assert parent.parent_incident_id is None

    # now web crashes (dependent) — links to db's incident
    web_down = ContainerState(id="1", name="web", status="exited", exit_code=1)
    web_watcher = Watcher(source=FakeSource([web_down]), store=store, bus=bus,
                          config=config, project=project)
    child = web_watcher.poll_once()[0]
    assert child.parent_incident_id == parent.id


def test_no_correlation_without_dependency_incident(store, bus, config):
    from digdown.watcher import Watcher
    from digdown.discovery import Project
    from tests.conftest import FakeSource
    project = Project(services=["web", "db"], depends_on={"web": ["db"], "db": []})
    web_down = ContainerState(id="1", name="web", status="exited", exit_code=1)
    w = Watcher(source=FakeSource([web_down]), store=store, bus=bus,
                config=config, project=project)
    inc = w.poll_once()[0]
    assert inc.parent_incident_id is None


class _StubProbe:
    name = "stub"

    def __init__(self, signals):
        self._signals = signals

    def poll(self):
        return list(self._signals)

    def verify(self, signal):
        return None


def test_probe_signal_with_valid_target_opens_incident(store, bus, config):
    from digdown.models import Signal, SignalType
    from tests.conftest import FakeSource
    state = ContainerState(id="1", name="web", status="running",
                           health=HealthStatus.HEALTHY)
    sig = Signal(source="http", type=SignalType.HTTP_ERROR, target="web",
                 check="home", detail={"status_code": 500})
    w = Watcher(source=FakeSource([state]), store=store, bus=bus, config=config,
                probes=[_StubProbe([sig])])
    opened = w.poll_once()
    assert len(opened) == 1
    assert opened[0].signal.type == SignalType.HTTP_ERROR


def test_probe_signal_with_unbound_target_is_dropped(store, bus, config):
    from digdown.models import Signal, SignalType
    from tests.conftest import FakeSource
    state = ContainerState(id="1", name="web", status="running")
    sig = Signal(source="http", type=SignalType.HTTP_ERROR, target="ghost", check="x")
    w = Watcher(source=FakeSource([state]), store=store, bus=bus, config=config,
                probes=[_StubProbe([sig])])
    assert w.poll_once() == []
    assert any(e["type"] == "signal_dropped" for e in bus.events)


def test_two_http_checks_same_target_dedup_separately(store, bus, config):
    from digdown.models import Signal, SignalType
    from tests.conftest import FakeSource
    state = ContainerState(id="1", name="web", status="running")
    s1 = Signal(source="http", type=SignalType.HTTP_ERROR, target="web", check="home")
    s2 = Signal(source="http", type=SignalType.HTTP_ERROR, target="web", check="api")
    w = Watcher(source=FakeSource([state]), store=store, bus=bus, config=config,
                probes=[_StubProbe([s1, s2])])
    opened = w.poll_once()
    assert len(opened) == 2


def test_poll_once_drops_muted_target(store, bus, config):
    # A muted target's failures never become incidents — they're dropped before
    # creation and reported as a `signal_dropped` event (reason: muted).
    from tests.conftest import FakeSource
    store.add_mute("blog")  # prefix → whole blog-* stack
    crash = ContainerState(id="1", name="blog-web_app-1", status="exited", exit_code=1)
    source = FakeSource([crash])
    w = Watcher(source=source, store=store, bus=bus, config=config)
    opened = w.poll_once()
    assert opened == []
    assert store.list_incidents() == []
    dropped = [e for e in bus.events
               if e.get("type") == "signal_dropped" and e.get("reason") == "muted"]
    assert len(dropped) == 1
    assert dropped[0]["signal"]["target"] == "blog-web_app-1"


def test_poll_once_still_opens_unmuted_target(store, bus, config):
    # Muting blog must not silence other containers.
    from tests.conftest import FakeSource
    store.add_mute("blog")
    frame = [ContainerState(id="1", name="blog-api_app-1", status="exited", exit_code=1),
             ContainerState(id="2", name="grafana", status="exited", exit_code=1)]
    w = Watcher(source=FakeSource(frame), store=store, bus=bus, config=config)
    opened = w.poll_once()
    assert [i.target for i in opened] == ["grafana"]


def test_detect_memory_pressure(store, bus, config):
    w = _watcher(store, bus, config)
    s = ContainerState(id="1", name="api", status="running",
                       mem_bytes=240_000_000, mem_limit_bytes=256_000_000, mem_percent=94.0)
    sigs = w.detect([s])
    assert [x.type for x in sigs] == [SignalType.MEMORY_PRESSURE]
    assert sigs[0].detail["percent"] == 94.0


def test_no_memory_pressure_below_threshold_or_without_limit(store, bus, config):
    w = _watcher(store, bus, config)
    below = ContainerState(id="1", name="api", status="running",
                           mem_bytes=10, mem_limit_bytes=256_000_000, mem_percent=40.0)
    nolimit = ContainerState(id="2", name="free", status="running",
                             mem_bytes=9_000_000_000, mem_limit_bytes=None, mem_percent=None)
    assert w.detect([below]) == []
    assert w.detect([nolimit]) == []


def test_memory_pressure_threshold_read_live(store, bus, config):
    w = _watcher(store, bus, config)
    s = ContainerState(id="1", name="api", status="running",
                       mem_bytes=1, mem_limit_bytes=100, mem_percent=85.0)
    assert w.detect([s]) == []          # 85 < default 90
    config.mem_percent_threshold = 80.0  # live edit
    assert [x.type for x in w.detect([s])] == [SignalType.MEMORY_PRESSURE]


def test_poll_once_records_into_stats_ring(store, bus, config):
    from digdown.stats_ring import StatsRing

    class _Src:
        def list_states(self):
            return [ContainerState(id="1", name="web", status="running",
                                   cpu_percent=12.0, mem_percent=80.0)]

    ring = StatsRing()
    w = Watcher(source=_Src(), store=store, bus=bus, config=config, stats_ring=ring)
    w.poll_once()
    assert ring.series("web") == {"cpu": [12.0], "mem": [80.0]}


def test_poll_once_records_host_vitals(store, bus, config):
    from digdown.stats_ring import HostRing

    class _Src:
        def list_states(self):
            return []

    ring = HostRing()
    w = Watcher(source=_Src(), store=store, bus=bus, config=config,
                host_ring=ring,
                host_vitals=lambda: {"disk_percent": 90.0, "mem_percent": 50.0})
    w.poll_once()
    assert ring.series() == {"disk": [90.0], "mem": [50.0]}


def _stuck_crash():
    return ContainerState(id="1", name="worker", status="exited", exit_code=1)


@pytest.mark.parametrize("ending", [
    IncidentState.CLOSED_NO_FIX, IncidentState.REJECTED,
    IncidentState.ROLLED_BACK, IncidentState.FAILED,
])
def test_still_broken_after_closing_does_not_reopen_every_poll(store, bus, config, ending):
    """Each new incident costs an AI diagnosis, so a container that stays down must
    not start a fresh one every poll once a person or the pipeline has closed it."""
    from tests.conftest import FakeSource
    crash = _stuck_crash()
    w = Watcher(source=FakeSource([crash]), store=store, bus=bus, config=config)
    first = w.poll_once()
    store.update_state(first[0].id, ending)
    assert w.poll_once() == []
    assert w.poll_once() == []
    assert len(store.list_incidents()) == 1


def test_stays_closed_across_a_restart_of_digdown(store, bus, config):
    from tests.conftest import FakeSource
    crash = _stuck_crash()
    first = Watcher(source=FakeSource([crash]), store=store, bus=bus,
                    config=config).poll_once()
    store.update_state(first[0].id, IncidentState.CLOSED_NO_FIX)
    restarted = Watcher(source=FakeSource([crash]), store=store, bus=bus, config=config)
    assert restarted.poll_once() == []


def test_reopens_when_the_problem_clears_and_comes_back(store, bus, config):
    from tests.conftest import FakeSource
    crash = _stuck_crash()
    healthy = ContainerState(id="1", name="worker", status="running")
    w = Watcher(source=FakeSource([crash], *[[healthy]] * 4, [crash]), store=store, bus=bus,
                config=config)
    first = w.poll_once()
    store.update_state(first[0].id, IncidentState.CLOSED_NO_FIX)
    for _ in range(4):
        assert w.poll_once() == []      # healthy for long enough to count as recovered
    again = w.poll_once()               # broke again: that's a new problem
    assert len(again) == 1 and again[0].id != first[0].id


# ---- a problem that goes away closes its own incident ----

def _clears(store, bus, config, state, source_frames):
    from tests.conftest import FakeSource
    w = Watcher(source=FakeSource(*source_frames), store=store, bus=bus, config=config)
    first = w.poll_once()
    store.update_state(first[0].id, state)
    for _ in range(len(source_frames) - 1):
        w.poll_once()
    return store.get_incident(first[0].id)


@pytest.mark.parametrize("state", [IncidentState.DETECTED, IncidentState.DIAGNOSED,
                                   IncidentState.DIAGNOSIS_FAILED, IncidentState.PROPOSED])
def test_incident_closes_once_the_container_has_recovered(store, bus, config, state):
    healthy = ContainerState(id="1", name="worker", status="running")
    frames = [[_stuck_crash()]] + [[healthy]] * 5
    assert _clears(store, bus, config, state, frames).state == IncidentState.RESOLVED


def test_a_brief_recovery_does_not_close_the_incident(store, bus, config):
    healthy = ContainerState(id="1", name="worker", status="running")
    frames = [[_stuck_crash()], [healthy], [_stuck_crash()], [healthy], [_stuck_crash()]]
    assert _clears(store, bus, config, IncidentState.DIAGNOSED, frames).state == IncidentState.DIAGNOSED


def test_work_in_progress_is_not_closed_underneath_it(store, bus, config):
    healthy = ContainerState(id="1", name="worker", status="running")
    frames = [[_stuck_crash()]] + [[healthy]] * 6
    for state in (IncidentState.INVESTIGATING, IncidentState.EXECUTING, IncidentState.VERIFYING):
        assert _clears(store, bus, config, state, frames).state == state
        store._conn.execute("DELETE FROM incidents")


def test_closing_tells_the_dashboard(store, bus, config):
    healthy = ContainerState(id="1", name="worker", status="running")
    frames = [[_stuck_crash()]] + [[healthy]] * 5
    _clears(store, bus, config, IncidentState.DETECTED, frames)
    updates = [e for e in bus.events if e["type"] == "incident_updated"]
    assert updates and updates[-1]["incident"]["state"] == "resolved"


# ---- a container still crash-looping hasn't recovered, however quiet it looks ----

def _flapping(restart_count, status="restarting"):
    return ContainerState(id="1", name="flapper", status=status, restart_count=restart_count)


def _loop_then(store, bus, config, later):
    from tests.conftest import FakeSource
    frames = [[_flapping(n)] for n in range(4)] + later
    w = Watcher(source=FakeSource(*frames), store=store, bus=bus, config=config)
    opened = []
    for _ in range(4):
        opened += w.poll_once()
    assert [i.signal.type for i in opened] == [SignalType.RESTART_LOOP]
    return w, opened[0]


def test_slow_crash_loop_does_not_count_as_recovered(store, bus, config):
    """Docker backs off to a minute between restarts, so the signal goes quiet while
    the container is still broken. Seen for real in the sandbox's flapper."""
    still_looping = [[_flapping(4)], [_flapping(4)], [_flapping(5)], [_flapping(5)], [_flapping(5)],
                     [_flapping(6)], [_flapping(6)], [_flapping(6)]]
    w, incident = _loop_then(store, bus, config, still_looping)
    store.update_state(incident.id, IncidentState.DIAGNOSED)
    for _ in still_looping:
        w.poll_once()
    assert store.get_incident(incident.id).state == IncidentState.DIAGNOSED


def test_slow_crash_loop_does_not_reopen_once_closed(store, bus, config):
    """Otherwise each quiet spell means another paid diagnosis of the same loop."""
    later = []
    for n in range(4, 40):
        later += [[_flapping(n)], [_flapping(n)], [_flapping(n)]]
    w, incident = _loop_then(store, bus, config, later)
    store.update_state(incident.id, IncidentState.CLOSED_NO_FIX)
    for _ in later:
        w.poll_once()
    assert len(store.list_incidents()) == 1


def test_a_settled_container_counts_as_recovered(store, bus, config):
    settled = [[_flapping(4, status="running")]] * 6
    w, incident = _loop_then(store, bus, config, settled)
    store.update_state(incident.id, IncidentState.DIAGNOSED)
    for _ in settled:
        w.poll_once()
    assert store.get_incident(incident.id).state == IncidentState.RESOLVED


def test_one_healthy_check_is_not_enough_to_call_it_a_new_problem(store, bus, config):
    from tests.conftest import FakeSource
    crash, healthy = _stuck_crash(), ContainerState(id="1", name="worker", status="running")
    w = Watcher(source=FakeSource([crash], [healthy], [crash]), store=store, bus=bus, config=config)
    first = w.poll_once()
    store.update_state(first[0].id, IncidentState.CLOSED_NO_FIX)
    assert w.poll_once() == []
    assert w.poll_once() == []


# ---- containers someone stopped on purpose ----

class _StoppableSource:
    def __init__(self, state, on_purpose):
        self.state, self.on_purpose, self.asked = state, on_purpose, 0

    def list_states(self):
        return [self.state]

    def stopped_on_purpose(self, target):
        self.asked += 1
        if isinstance(self.on_purpose, Exception):
            raise self.on_purpose
        return self.on_purpose


def _stopped_watcher(store, bus, config, on_purpose, **state):
    s = ContainerState(id="1", name="web", status="exited", exit_code=137, **state)
    source = _StoppableSource(s, on_purpose)
    return Watcher(source, store, bus, config), source


def test_a_deliberate_stop_opens_no_incident(store, bus, config):
    watcher, _ = _stopped_watcher(store, bus, config, True)
    assert watcher.poll_once() == []


def test_a_real_crash_still_opens_an_incident(store, bus, config):
    watcher, _ = _stopped_watcher(store, bus, config, False)
    assert [i.signal.type for i in watcher.poll_once()] == [SignalType.CRASH]


def test_running_out_of_memory_is_never_taken_for_a_deliberate_stop(store, bus, config):
    watcher, source = _stopped_watcher(store, bus, config, True, oom_killed=True)
    assert [i.signal.type for i in watcher.poll_once()] == [SignalType.OOM]
    assert source.asked == 0


def test_docker_is_only_asked_once_per_stop(store, bus, config):
    watcher, source = _stopped_watcher(store, bus, config, True)
    watcher.poll_once()
    watcher.poll_once()
    assert source.asked == 1


def test_if_docker_cannot_say_it_counts_as_a_failure(store, bus, config):
    watcher, _ = _stopped_watcher(store, bus, config, RuntimeError("no events"))
    assert [i.signal.type for i in watcher.poll_once()] == [SignalType.CRASH]


# ---- a failure linked to another one, once that one is over ----

def _linked(store, parent_state, ended_ago_seconds=3600):
    from datetime import timedelta
    from digdown.models import Incident, Signal, utcnow
    sig = Signal(source="docker", type=SignalType.CRASH, target="db")
    parent = store.add_incident(Incident(source="docker", target="db", signal=sig,
                                         dedup_key="parent", state=parent_state))
    store._conn.execute("UPDATE incidents SET updated_at = ? WHERE id = ?",
                        ((utcnow() - timedelta(seconds=ended_ago_seconds)).isoformat(), parent.id))
    store._conn.commit()
    sig2 = Signal(source="docker", type=SignalType.CRASH, target="app")
    return store.add_incident(Incident(source="docker", target="app", signal=sig2,
                                       dedup_key=sig2.dedup_key, parent_incident_id=parent.id))


def _down_app():
    return ContainerState(id="2", name="app", status="exited", exit_code=1)


@pytest.mark.parametrize("ended", [IncidentState.RESOLVED, IncidentState.ROLLED_BACK,
                                   IncidentState.CLOSED_NO_FIX, IncidentState.REJECTED,
                                   IncidentState.DIAGNOSED])  # all explain-only mode does
def test_a_linked_failure_gets_its_own_diagnosis_once_the_first_is_over(store, bus, config, ended):
    linked = _linked(store, ended)
    watcher = Watcher(type("S", (), {"list_states": lambda self: [_down_app()]})(), store, bus, config)
    released = watcher.poll_once()
    assert [i.id for i in released] == [linked.id]
    assert released[0].parent_incident_id is None
    assert store.get_incident(linked.id).parent_incident_id is None


def test_a_linked_failure_keeps_waiting_while_the_first_is_open(store, bus, config):
    _linked(store, IncidentState.PROPOSED)
    watcher = Watcher(type("S", (), {"list_states": lambda self: [_down_app()]})(), store, bus, config)
    assert watcher.poll_once() == []


def test_a_linked_failure_gets_time_to_recover_by_itself_first(store, bus, config):
    # Fixing a database usually brings the app back, so don't spend an AI call at once.
    _linked(store, IncidentState.RESOLVED, ended_ago_seconds=1)
    watcher = Watcher(type("S", (), {"list_states": lambda self: [_down_app()]})(), store, bus, config)
    assert watcher.poll_once() == []


def test_a_linked_failure_is_only_released_once(store, bus, config):
    _linked(store, IncidentState.RESOLVED)
    watcher = Watcher(type("S", (), {"list_states": lambda self: [_down_app()]})(), store, bus, config)
    assert len(watcher.poll_once()) == 1
    assert watcher.poll_once() == []


# ---- linking failures under normal Compose names ----

class _Project:
    name = "shop"
    services = ["db", "app"]
    depends_on = {"app": ["db"], "db": []}


def _compose_state(name, service, status="exited"):
    return ContainerState(id=name, name=name, service=service, project="shop", status=status,
                          exit_code=1 if status == "exited" else None)


def test_failures_link_under_normal_compose_container_names(store, bus, config):
    class Source:
        def __init__(self):
            self.frames = [[_compose_state("shop-db-1", "db"),
                            _compose_state("shop-app-1", "app", "running")],
                           [_compose_state("shop-db-1", "db"),
                            _compose_state("shop-app-1", "app")]]

        def list_states(self):
            return self.frames.pop(0)

    watcher = Watcher(Source(), store, bus, config, project=_Project())
    db = watcher.poll_once()[0]
    app = watcher.poll_once()[0]
    assert app.target == "shop-app-1" and app.parent_incident_id == db.id


def test_failures_do_not_link_across_stacks_that_share_service_names(store, bus, config):
    other_db = ContainerState(id="o", name="notes-db-1", service="db", project="notes",
                              status="exited", exit_code=1)

    class Source:
        def __init__(self):
            self.frames = [[other_db, _compose_state("shop-app-1", "app", "running")],
                           [other_db, _compose_state("shop-app-1", "app")]]

        def list_states(self):
            return self.frames.pop(0)

    watcher = Watcher(Source(), store, bus, config, project=_Project())
    watcher.poll_once()
    app = watcher.poll_once()[0]
    assert app.parent_incident_id is None


# ---- found in real use: a container stuck restarting for a long time raised nothing ----

def _looping(restarts, status="restarting"):
    return ContainerState(id="1", name="app", status=status, exit_code=1, restart_count=restarts)


class _Frames:
    def __init__(self, *frames):
        self.frames = list(frames)

    def list_states(self):
        return [self.frames.pop(0)]


def test_a_slow_crash_loop_is_noticed(store, bus, config):
    # Docker waits up to a minute between restarts, so the count barely moves from
    # one check to the next, but the container is "restarting" check after check.
    watcher = Watcher(_Frames(_looping(900), _looping(900)), store, bus, config)
    watcher.poll_once()
    assert [i.signal.type for i in watcher.poll_once()] == [SignalType.RESTART_LOOP]


def test_one_restart_after_a_long_quiet_spell_is_not_a_loop(store, bus, config):
    watcher = Watcher(_Frames(_looping(1), _looping(1)), store, bus, config)
    watcher.poll_once()
    assert watcher.poll_once() == []


def test_catching_a_container_mid_restart_once_is_not_a_loop(store, bus, config):
    watcher = Watcher(_Frames(_looping(900), _looping(900, "running")), store, bus, config)
    assert watcher.poll_once() == [] and watcher.poll_once() == []


def test_a_stopped_container_is_not_also_reported_unhealthy(store, bus, config):
    # Docker keeps the last health result on a stopped container.
    stopped = ContainerState(id="1", name="db", status="exited", exit_code=0,
                             health=HealthStatus.UNHEALTHY)
    watcher = Watcher(_Frames(stopped), store, bus, config)
    assert [i.signal.type for i in watcher.poll_once()] == [SignalType.STOPPED]


def test_grafana_incidents_are_left_to_grafana(store, bus, config):
    from digdown.models import Incident, Signal, SignalType
    from tests.conftest import FakeSource
    sig = Signal(source="grafana", type=SignalType.ALERT_FIRING, target="remote-app")
    inc = store.add_incident(Incident(source="grafana", target="remote-app", signal=sig,
                                      dedup_key="grafana:x", state=IncidentState.DIAGNOSED))
    w = Watcher(source=FakeSource([]), store=store, bus=bus, config=config)
    for _ in range(6):
        w.poll_once()
    assert store.get_incident(inc.id).state == IncidentState.DIAGNOSED
