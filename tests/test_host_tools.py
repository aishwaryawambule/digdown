from digdown.host_tools import system_report, _default_runner
import digdown.host_tools as ht
from digdown.models import ContainerState


class _FakeSource:
    def __init__(self, states):
        self._states = states
    def list_states(self):
        return self._states


def _runner(mapping):
    return lambda argv: mapping.get(tuple(argv), "")


def test_report_has_all_sections_and_sorted_container_table():
    states = [
        ContainerState(id="1", name="api", status="running", cpu_percent=12.0, mem_percent=80.0),
        ContainerState(id="2", name="db", status="running", cpu_percent=3.0, mem_percent=20.0),
    ]
    m = {
        ("uptime",): " 14:05  load average: 0.40, 0.30, 0.20\n",
        ("free", "-h"): "      total  used  free\nMem:  8.0G   6.0G  1.0G\n",
        ("df", "-h", "/"): "Filesystem Size Used Avail Use%\n/dev/sda1 100G 90G 10G 90%\n",
        ("du", "-xh", "--max-depth=2", "/"): "2.0G\t/var/log\n80G\t/var/lib/docker\n1.0G\t/opt\n",
        ("docker", "system", "df"): "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\nImages 12 5 8GB 3GB\n",
        ("ps", "-eo", "pid,pcpu,pmem,rss,comm", "--sort=-pcpu"): "PID %CPU %MEM RSS COMMAND\n1 9.0 5.0 100 python\n",
    }
    r = system_report(_FakeSource(states), "/", runner=_runner(m))
    for sec in ("[LOAD]", "[MEMORY]", "[DISK]", "[TOP PROCESSES]", "[CONTAINERS]"):
        assert sec in r
    assert "load average" in r and "Mem:" in r and "/var/lib/docker" in r and "Images" in r and "python" in r
    assert r.index("api") < r.index("db")          # container table sorted by mem% desc
    assert "12.0" in r and "80.0" in r


def test_du_sorted_by_size_desc_and_capped():
    m = {("du", "-xh", "--max-depth=2", "/"): "1.0G\t/opt\n38G\t/var/lib/docker\n2.0G\t/var/log\n"}
    r = system_report(_FakeSource([]), "/", runner=_runner(m), max_section_lines=2)
    disk = r.split("[DISK]")[1].split("[")[0]
    assert disk.index("/var/lib/docker") < disk.index("/var/log")   # biggest first
    assert "/opt" not in disk                                       # dropped by cap=2


def test_unavailable_command_degrades_not_raises():
    r = system_report(_FakeSource([]), "/", runner=lambda argv: "")
    assert "[LOAD]" in r and "(unavailable)" in r
    assert "(no containers in scope)" in r


def test_none_stats_render_dash():
    states = [ContainerState(id="1", name="x", status="running")]  # cpu/mem None
    r = system_report(_FakeSource(states), "/", runner=lambda argv: "")
    cont = r.split("[CONTAINERS]")[1]
    assert "x" in cont and "-" in cont


def test_default_runner_returns_str_and_swallows_missing_binary():
    run = _default_runner(timeout=3.0)
    assert isinstance(run(["uptime"]), str)                 # real cmd → str (maybe empty)
    assert run(["this-binary-does-not-exist-xyz"]) == ""    # missing → swallowed


def test_list_states_raising_degrades_not_raises():
    class _BadSource:
        def list_states(self):
            raise RuntimeError("docker unreachable")
    r = system_report(_BadSource(), "/", runner=lambda argv: "")
    assert "[CONTAINERS]" in r          # degraded, still a string — never raised


def test_host_vitals_reads_disk_load_mem(monkeypatch, tmp_path):
    from collections import namedtuple
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(ht.shutil, "disk_usage", lambda p: Usage(1000, 900, 100))
    monkeypatch.setattr(ht.os, "getloadavg", lambda: (1.25, 0.8, 0.5))
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       1000 kB\nMemAvailable:    250 kB\n")
    monkeypatch.setattr(ht, "_MEMINFO", str(meminfo))
    v = ht.host_vitals("/")
    assert v["disk_percent"] == 90.0      # 900/1000
    assert v["load1"] == 1.25
    assert v["mem_percent"] == 75.0       # (1 - 250/1000) * 100


def test_host_vitals_degrades_each_field_to_none(monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(ht.shutil, "disk_usage", boom)
    monkeypatch.setattr(ht.os, "getloadavg", boom)
    monkeypatch.setattr(ht, "_MEMINFO", "/no/such/meminfo")
    assert ht.host_vitals("/") == {"disk_percent": None, "load1": None, "mem_percent": None}
