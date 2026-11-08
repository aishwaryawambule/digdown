import pytest

from digdown.write_adapter import DockerWriter
from digdown.plan_models import Action, ActionType


class FakeContainer:
    def __init__(self, name, mem_limit=0, memswap=0):
        self.name = name
        self.labels = {"com.docker.compose.service": name}
        self.attrs = {"State": {"Status": "running"},
                      "Config": {"Image": "web:1"},
                      "HostConfig": {"Memory": mem_limit, "MemorySwap": memswap},
                      "Image": "sha256:deadbeef"}
        self.calls = []

    def restart(self, timeout=10):
        self.calls.append("restart")

    def start(self):
        self.calls.append("start")

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))


class FakeClient:
    def __init__(self, containers):
        c = containers
        class _C:
            list = staticmethod(lambda all=True: c)
        self.containers = _C()


def _writer(tmp_path, container):
    runs = []
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n")
    w = DockerWriter(FakeClient([container]), compose_path=str(tmp_path / "dc.yml"),
                     env_path=str(env), compose_runner=lambda args: runs.append(args))
    return w, runs, env


def test_apply_restart_calls_container(tmp_path):
    c = FakeContainer("web")
    w, _, _ = _writer(tmp_path, c)
    w.apply(Action(type=ActionType.RESTART, target="web"))
    assert c.calls == ["restart"]


def test_apply_start_calls_container(tmp_path):
    c = FakeContainer("web")
    w, _, _ = _writer(tmp_path, c)
    w.apply(Action(type=ActionType.START, target="web"))
    assert c.calls == ["start"]


def test_apply_recreate_runs_compose(tmp_path):
    c = FakeContainer("web")
    w, runs, _ = _writer(tmp_path, c)
    w.apply(Action(type=ActionType.RECREATE, target="web"))
    assert any("--force-recreate" in a and "web" in a for a in runs)


def test_apply_set_env_var_writes_file_and_recreates(tmp_path):
    c = FakeContainer("web")
    w, runs, env = _writer(tmp_path, c)
    w.apply(Action(type=ActionType.SET_ENV_VAR, target="web",
                   params={"key": "API_KEY", "value": "xyz"}))
    assert "API_KEY=xyz" in env.read_text()
    assert runs  # forced a recreate to pick up the change


def test_capture_records_image_and_env(tmp_path):
    c = FakeContainer("web")
    w, _, _ = _writer(tmp_path, c)
    snap = w.capture("web")
    assert snap["status"] == "running"
    assert "EXISTING=1" in snap["env_backup"]


def test_rollback_set_env_restores_file(tmp_path):
    c = FakeContainer("web")
    w, runs, env = _writer(tmp_path, c)
    a = Action(type=ActionType.SET_ENV_VAR, target="web",
               params={"key": "API_KEY", "value": "xyz"})
    snap = w.capture("web")            # backs up the original env
    w.apply(a)
    assert "API_KEY=xyz" in env.read_text()
    w.rollback(a, snap)
    assert "API_KEY=xyz" not in env.read_text()   # restored
    assert "EXISTING=1" in env.read_text()


def test_apply_set_resource_limit_updates_live(tmp_path):
    c = FakeContainer("web", mem_limit=256_000_000, memswap=256_000_000)
    w, runs, _ = _writer(tmp_path, c)
    w.apply(Action(type=ActionType.SET_RESOURCE_LIMIT, target="web",
                   params={"mem_limit_bytes": 512_000_000}))
    assert ("update", {"mem_limit": 512_000_000, "memswap_limit": 512_000_000}) in c.calls
    assert runs == []   # SDK-direct: no compose


def test_apply_set_resource_limit_refuses_unlimited(tmp_path):
    c = FakeContainer("web", mem_limit=0)            # unlimited
    w, _, _ = _writer(tmp_path, c)
    with pytest.raises(ValueError):
        w.apply(Action(type=ActionType.SET_RESOURCE_LIMIT, target="web",
                       params={"mem_limit_bytes": 512_000_000}))
    assert c.calls == []   # never touched at all


def test_apply_set_resource_limit_refuses_non_increasing(tmp_path):
    c = FakeContainer("web", mem_limit=512_000_000)
    w, _, _ = _writer(tmp_path, c)
    with pytest.raises(ValueError):
        w.apply(Action(type=ActionType.SET_RESOURCE_LIMIT, target="web",
                       params={"mem_limit_bytes": 256_000_000}))
    assert c.calls == []


def test_apply_set_resource_limit_refuses_equal(tmp_path):
    c = FakeContainer("web", mem_limit=512_000_000)
    w, _, _ = _writer(tmp_path, c)
    with pytest.raises(ValueError):
        w.apply(Action(type=ActionType.SET_RESOURCE_LIMIT, target="web",
                       params={"mem_limit_bytes": 512_000_000}))
    assert c.calls == []


def test_capture_records_mem_limits(tmp_path):
    c = FakeContainer("web", mem_limit=256_000_000, memswap=256_000_000)
    w, _, _ = _writer(tmp_path, c)
    snap = w.capture("web")
    assert snap["mem_limit_bytes"] == 256_000_000
    assert snap["memswap_limit_bytes"] == 256_000_000


def test_rollback_set_resource_limit_restores(tmp_path):
    c = FakeContainer("web", mem_limit=256_000_000, memswap=256_000_000)
    w, _, _ = _writer(tmp_path, c)
    a = Action(type=ActionType.SET_RESOURCE_LIMIT, target="web",
               params={"mem_limit_bytes": 512_000_000})
    snap = w.capture("web")
    w.apply(a)
    w.rollback(a, snap)
    assert ("update", {"mem_limit": 256_000_000, "memswap_limit": 256_000_000}) in c.calls


def _named(name, service):
    c = FakeContainer(name)
    c.labels = {"com.docker.compose.service": service}
    return c


def test_a_fix_goes_to_the_exact_container_even_if_a_service_shares_the_name(tmp_path):
    other, wanted = _named("other-app-1", "db"), _named("db", "database")
    w = DockerWriter(FakeClient([other, wanted]), "c.yml", compose_runner=lambda a: None)
    w.apply(Action(type=ActionType.RESTART, target="db"))
    assert wanted.calls == ["restart"] and other.calls == []


def test_a_service_name_in_two_stacks_refuses_rather_than_guessing(tmp_path):
    one, two = _named("immich-db-1", "db"), _named("nextcloud-db-1", "db")
    w = DockerWriter(FakeClient([one, two]), "c.yml", compose_runner=lambda a: None)
    with pytest.raises(LookupError, match="more than one"):
        w.apply(Action(type=ActionType.RESTART, target="db"))
    assert one.calls == [] and two.calls == []
