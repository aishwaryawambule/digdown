from digdown.stats_ring import StatsRing, MAX_SAMPLES
from digdown.models import ContainerState


def _state(name, cpu=None, mem=None):
    return ContainerState(id=name, name=name, status="running",
                          cpu_percent=cpu, mem_percent=mem)


def test_default_capacity_is_60():
    assert MAX_SAMPLES == 60


def test_record_and_series_oldest_to_newest():
    r = StatsRing()
    r.record([_state("web", 10.0, 40.0)])
    r.record([_state("web", 20.0, 50.0)])
    assert r.series("web") == {"cpu": [10.0, 20.0], "mem": [40.0, 50.0]}


def test_series_unknown_is_empty():
    assert StatsRing().series("nope") == {"cpu": [], "mem": []}


def test_caps_at_max_samples():
    r = StatsRing(max_samples=3)
    for i in range(5):
        r.record([_state("web", float(i), None)])
    assert r.series("web")["cpu"] == [2.0, 3.0, 4.0]   # oldest two evicted, newest three kept


def test_prunes_vanished_containers():
    r = StatsRing()
    r.record([_state("web"), _state("db")])
    r.record([_state("web")])           # db gone this poll
    assert r.series("db") == {"cpu": [], "mem": []}
    assert r.series("web")["cpu"] == [None, None]


def test_none_samples_preserved():
    r = StatsRing()
    r.record([_state("web", None, None)])
    assert r.series("web") == {"cpu": [None], "mem": [None]}


def test_record_empty_states_prunes_all():
    r = StatsRing()
    r.record([_state("web"), _state("db")])
    r.record([])                       # nothing running this poll → all pruned
    assert r.series("web") == {"cpu": [], "mem": []}
    assert r.series("db") == {"cpu": [], "mem": []}


def test_host_ring_records_and_series():
    from digdown.stats_ring import HostRing
    r = HostRing()
    r.record(80.0, 40.0)
    r.record(85.0, 45.0)
    assert r.series() == {"disk": [80.0, 85.0], "mem": [40.0, 45.0]}


def test_host_ring_caps_at_max_samples():
    from digdown.stats_ring import HostRing
    r = HostRing(max_samples=2)
    for i in range(3):
        r.record(float(i), None)
    assert r.series()["disk"] == [1.0, 2.0]
