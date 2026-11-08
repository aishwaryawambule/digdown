from digdown.docker_adapter import DockerAdapter, compute_stats
from digdown.models import HealthStatus


class FakeContainer:
    def __init__(self, name, attrs, labels=None, image_tag="img:latest"):
        self.id = "id-" + name           # unique per container (inspect maps by id)
        self.name = name
        self.attrs = attrs
        self.labels = labels or {}
        self.image_tag = image_tag
        class _Img:
            tags = [image_tag]
        self.image = _Img()


class FakeApi:
    """The low-level client.api used by list_states: a single summary list plus
    a per-id inspect. Records which ids were inspected so tests can assert that
    out-of-scope containers are never inspected."""

    def __init__(self, containers):
        self._containers = containers
        self.inspected: list[str] = []

    def containers(self, all=True):
        return [
            {"Id": c.id, "Names": ["/" + c.name], "Image": c.image_tag,
             "Labels": c.labels,
             "State": (c.attrs.get("State") or {}).get("Status", "")}
            for c in self._containers
        ]

    def inspect_container(self, cid):
        self.inspected.append(cid)
        for c in self._containers:
            if c.id == cid:
                a = dict(c.attrs)
                a["Id"] = c.id
                a.setdefault("Name", "/" + c.name)
                cfg = dict(a.get("Config") or {})
                cfg.setdefault("Labels", c.labels)
                a["Config"] = cfg
                return a
        return {}


class FakeClient:
    def __init__(self, containers):
        self._containers = containers
        class _C:
            list = staticmethod(lambda all=True: containers)
        self.containers = _C()
        self.api = FakeApi(containers)


def test_maps_running_healthy_container():
    c = FakeContainer(
        "web",
        attrs={"State": {"Status": "running", "ExitCode": 0, "OOMKilled": False,
                          "Health": {"Status": "healthy"}}, "RestartCount": 0},
        labels={"com.docker.compose.service": "web"},
    )
    states = DockerAdapter(FakeClient([c])).list_states()
    assert len(states) == 1
    s = states[0]
    assert s.name == "web"
    assert s.service == "web"
    assert s.status == "running"
    assert s.health == HealthStatus.HEALTHY
    assert s.image == "img:latest"


def test_maps_exited_oom_container_without_healthcheck():
    c = FakeContainer(
        "worker",
        attrs={"State": {"Status": "exited", "ExitCode": 137, "OOMKilled": True},
               "RestartCount": 5},
    )
    s = DockerAdapter(FakeClient([c])).list_states()[0]
    assert s.status == "exited"
    assert s.exit_code == 137
    assert s.oom_killed is True
    assert s.restart_count == 5
    assert s.health == HealthStatus.NONE   # no Health key → NONE
    assert s.service is None


class FakeLoggingContainer(FakeContainer):
    def __init__(self, name, attrs, labels=None, logs=b"log line\n"):
        super().__init__(name, attrs, labels)
        self._logs = logs

    def logs(self, tail=200):
        return self._logs


def test_get_logs_decodes_bytes():
    c = FakeLoggingContainer("web", attrs={"State": {}}, logs=b"boom\ntrace\n")
    out = DockerAdapter(FakeClient([c])).get_logs("web", lines=10)
    assert "trace" in out


def test_inspect_extracts_config_and_env_names():
    c = FakeLoggingContainer(
        "web",
        attrs={"State": {}, "Config": {"Image": "web:1", "Cmd": ["node", "app.js"],
                                       "Env": ["DB_HOST=db", "DB_PASSWORD=secret"]},
               "HostConfig": {"RestartPolicy": {"Name": "always"}}},
        labels={"com.docker.compose.service": "web"},
    )
    info = DockerAdapter(FakeClient([c])).inspect("web")
    assert info["image"] == "web:1"
    assert info["command"] == "node app.js"
    assert info["restart_policy"] == "always"
    assert info["env"]["DB_HOST"] == "db"          # values kept internally
    assert set(info["env"].keys()) == {"DB_HOST", "DB_PASSWORD"}


def test_inspect_unknown_container_returns_empty():
    assert DockerAdapter(FakeClient([])).inspect("nope") == {}


def test_project_scoping_filters_other_projects():
    mine = FakeContainer("web", attrs={"State": {"Status": "running"}},
                         labels={"com.docker.compose.project": "mine",
                                 "com.docker.compose.service": "web"})
    theirs = FakeContainer("other", attrs={"State": {"Status": "running"}},
                           labels={"com.docker.compose.project": "theirs"})
    scoped = DockerAdapter(FakeClient([mine, theirs]), project_name="mine")
    names = [s.name for s in scoped.list_states()]
    assert names == ["web"]


def test_exclude_projects_drops_them_when_watching_all():
    # No allowlist (watch everything) but exclude a manually-downed project.
    keep = FakeContainer("web", attrs={"State": {"Status": "running"}},
                         labels={"com.docker.compose.project": "shop"})
    blog = FakeContainer("blog-api", attrs={"State": {"Status": "exited",
                         "ExitCode": 0}},
                         labels={"com.docker.compose.project": "blog"})
    states = DockerAdapter(FakeClient([keep, blog]),
                           exclude_projects=["blog"]).list_states()
    assert [s.name for s in states] == ["web"]


def test_exclude_takes_precedence_over_allowlist():
    blog = FakeContainer("blog-api", attrs={"State": {"Status": "exited"}},
                         labels={"com.docker.compose.project": "blog"})
    graf = FakeContainer("grafana", attrs={"State": {"Status": "running"}},
                         labels={"com.docker.compose.project": "grafana"})
    states = DockerAdapter(FakeClient([blog, graf]),
                           project_name=["blog", "grafana"],
                           exclude_projects=["blog"]).list_states()
    assert [s.name for s in states] == ["grafana"]


def test_list_states_includes_restart_policy():
    c = FakeContainer("web", attrs={
        "State": {"Status": "exited", "ExitCode": 0},
        "HostConfig": {"RestartPolicy": {"Name": "always"}},
    })
    states = DockerAdapter(FakeClient([c])).list_states()
    assert states[0].restart_policy == "always"


def test_list_states_includes_compose_project():
    c = FakeContainer("web", attrs={"State": {"Status": "running"}},
                      labels={"com.docker.compose.project": "shop",
                              "com.docker.compose.service": "web"})
    states = DockerAdapter(FakeClient([c])).list_states()
    assert states[0].project == "shop"


def test_scoping_watches_multiple_projects():
    web = FakeContainer("web", attrs={"State": {"Status": "running"}},
                        labels={"com.docker.compose.project": "shop"})
    graf = FakeContainer("grafana", attrs={"State": {"Status": "running"}},
                         labels={"com.docker.compose.project": "grafana"})
    other = FakeContainer("other", attrs={"State": {"Status": "running"}},
                          labels={"com.docker.compose.project": "theirs"})
    scoped = DockerAdapter(FakeClient([web, graf, other]),
                           project_name=["shop", "grafana"])
    assert sorted(s.name for s in scoped.list_states()) == ["grafana", "web"]


def test_no_project_name_watches_all():
    a = FakeContainer("a", attrs={"State": {"Status": "running"}},
                      labels={"com.docker.compose.project": "x"})
    b = FakeContainer("b", attrs={"State": {"Status": "running"}}, labels={})
    assert len(DockerAdapter(FakeClient([a, b])).list_states()) == 2


def test_scoping_does_not_inspect_out_of_scope_containers():
    """The whole point of the remote-Docker optimization: out-of-scope
    containers are filtered from the cheap summary and never inspected."""
    mine = FakeContainer("web", attrs={"State": {"Status": "running"}},
                         labels={"com.docker.compose.project": "mine"})
    theirs = FakeContainer("other", attrs={"State": {"Status": "running"}},
                           labels={"com.docker.compose.project": "theirs"})
    client = FakeClient([mine, theirs])
    DockerAdapter(client, project_name="mine").list_states()
    assert client.api.inspected == ["id-web"]   # "id-other" is never inspected


def test_get_logs_passes_since_when_windowed():
    from digdown.docker_adapter import DockerAdapter

    captured = {}

    class _Container:
        name = "web"
        def logs(self, **kwargs):
            captured.update(kwargs)
            return b"line1\nline2\n"

    class _Client:
        class containers:
            @staticmethod
            def list(*a, **k):
                return [_Container()]

    a = DockerAdapter(_Client())
    out = a.get_logs("web", lines=50, since_seconds=60)
    assert "line1" in out
    assert captured["tail"] == 50
    assert "since" in captured and isinstance(captured["since"], int)


def test_get_logs_without_since_is_unchanged():
    from digdown.docker_adapter import DockerAdapter

    captured = {}

    class _Container:
        name = "web"
        def logs(self, **kwargs):
            captured.update(kwargs)
            return b"ok"

    class _Client:
        class containers:
            @staticmethod
            def list(*a, **k):
                return [_Container()]

    DockerAdapter(_Client()).get_logs("web", lines=10)
    assert captured == {"tail": 10}      # no `since` key when windowless


def test_compute_stats_cpu_and_mem():
    raw = {
        "memory_stats": {"usage": 250_000_000, "limit": 8_000_000_000,
                         "stats": {"inactive_file": 10_000_000}},
        "cpu_stats": {"cpu_usage": {"total_usage": 200, "percpu_usage": [1, 1]},
                      "system_cpu_usage": 2000, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
    }
    s = compute_stats(raw, limit_bytes=256_000_000)
    assert s["mem_bytes"] == 240_000_000            # 250M usage - 10M inactive_file
    assert s["mem_limit_bytes"] == 256_000_000      # the CONFIGURED limit, not 8G
    assert s["mem_percent"] == 93.8                 # 240/256 * 100, 1dp
    assert s["cpu_percent"] == 20.0                 # 100/1000 * 2 * 100


def test_compute_stats_no_limit_and_idle_cpu():
    # unlimited container (limit 0) -> mem_percent None; first/idle cpu sample -> 0.0
    raw = {"memory_stats": {"usage": 100},
           "cpu_stats": {"cpu_usage": {"total_usage": 5}, "system_cpu_usage": 10},
           "precpu_stats": {"cpu_usage": {"total_usage": 5}, "system_cpu_usage": 10}}
    s = compute_stats(raw, limit_bytes=0)
    assert s["mem_bytes"] == 100
    assert s["mem_limit_bytes"] is None
    assert s["mem_percent"] is None
    assert s["cpu_percent"] == 0.0                  # system_delta == 0


def test_compute_stats_handles_garbage_without_raising():
    assert compute_stats({}, 0) == {"cpu_percent": None, "mem_bytes": None,
                                    "mem_limit_bytes": None, "mem_percent": None}


def test_compute_stats_non_numeric_inactive_file_does_not_raise():
    raw = {"memory_stats": {"usage": 100, "stats": {"inactive_file": "bad"}}}
    s = compute_stats(raw, limit_bytes=256)
    assert s["mem_bytes"] == 100        # non-numeric inactive_file treated as 0
    assert s["mem_percent"] == 39.1     # 100/256 * 100


class _StatApi:
    """Low-level API fake with containers/inspect_container/stats."""
    def __init__(self, summaries, attrs_by_cid, stats_by_cid):
        self._summaries = summaries
        self._attrs = attrs_by_cid
        self._stats = stats_by_cid

    def containers(self, all=True):
        return self._summaries

    def inspect_container(self, cid):
        return self._attrs.get(cid, {})

    def stats(self, cid, stream=False):
        v = self._stats.get(cid)
        if isinstance(v, Exception):
            raise v
        return v


class _StatClient:
    def __init__(self, api):
        self.api = api


def _running_attrs(mem_limit):
    return {"Name": "/api", "State": {"Status": "running"},
            "Config": {"Labels": {}}, "HostConfig": {"Memory": mem_limit}}


def test_list_states_attaches_resource_stats_for_running():
    summaries = [{"Id": "abc", "Labels": {}, "Names": ["/api"], "Image": "api:1"}]
    attrs = {"abc": _running_attrs(256_000_000)}
    stats = {"abc": {"memory_stats": {"usage": 240_000_000, "stats": {"inactive_file": 0}},
                     "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000, "online_cpus": 1},
                     "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000}}}
    adapter = DockerAdapter(_StatClient(_StatApi(summaries, attrs, stats)))
    s = adapter.list_states()[0]
    assert s.mem_bytes == 240_000_000
    assert s.mem_limit_bytes == 256_000_000
    assert s.mem_percent == 93.8
    assert s.cpu_percent == 10.0                     # 100/1000 * 1 * 100


def test_list_states_stats_failure_is_swallowed():
    summaries = [{"Id": "abc", "Labels": {}, "Names": ["/api"], "Image": "api:1"}]
    attrs = {"abc": _running_attrs(256_000_000)}
    stats = {"abc": RuntimeError("container vanished")}
    adapter = DockerAdapter(_StatClient(_StatApi(summaries, attrs, stats)))
    s = adapter.list_states()[0]
    assert s.mem_bytes is None and s.cpu_percent is None


def test_list_states_skips_stats_when_disabled_or_not_running():
    summaries = [{"Id": "x", "Labels": {}, "Names": ["/down"], "Image": "i"}]
    attrs = {"x": {"Name": "/down", "State": {"Status": "exited", "ExitCode": 0},
                   "Config": {}, "HostConfig": {"Memory": 256_000_000}}}
    # exited container: never sampled even with stats present
    adapter = DockerAdapter(_StatClient(_StatApi(summaries, attrs, {"x": {"memory_stats": {"usage": 9}}})))
    assert adapter.list_states()[0].mem_bytes is None
    # disabled: running container not sampled
    attrs2 = {"x": _running_attrs(256_000_000)}
    a2 = DockerAdapter(_StatClient(_StatApi(summaries, attrs2, {"x": {"memory_stats": {"usage": 9}}})),
                       collect_resource_stats=False)
    assert a2.list_states()[0].mem_bytes is None


class _EventsClient:
    def __init__(self, events=None, error=None):
        self._events, self._error = events or [], error

    def events(self, **kwargs):
        if self._error:
            raise self._error
        self.kwargs = kwargs
        return iter([{"Action": a, "time": n} for n, a in enumerate(self._events)])


def _adapter_with_events(*actions, error=None):
    from digdown.docker_adapter import DockerAdapter
    adapter = DockerAdapter.__new__(DockerAdapter)
    adapter._client = _EventsClient(actions, error)
    return adapter


def test_docker_stop_counts_as_on_purpose():
    # Seen on real Docker: stopping an app that ignores the signal gives exit 137.
    assert _adapter_with_events("start", "kill", "kill", "stop", "die").stopped_on_purpose("web")


def test_docker_kill_counts_as_on_purpose():
    assert _adapter_with_events("start", "kill", "die").stopped_on_purpose("web")


def test_a_crash_does_not_count_as_on_purpose():
    assert not _adapter_with_events("start", "die").stopped_on_purpose("web")


def test_a_crash_after_an_old_stop_and_a_start_does_not_count():
    assert not _adapter_with_events("kill", "stop", "die", "start", "die").stopped_on_purpose("web")


def test_no_events_on_record_does_not_count():
    assert not _adapter_with_events().stopped_on_purpose("web")


def test_only_recent_events_are_asked_for_and_the_call_returns():
    adapter = _adapter_with_events("start", "die")
    adapter.stopped_on_purpose("web")
    assert adapter._client.kwargs["until"] > adapter._client.kwargs["since"]
    assert adapter._client.kwargs["filters"]["container"] == "web"


def test_a_stopped_container_still_reports_its_memory_limit():
    # Found on real Docker: stats only cover running containers, so this was missing
    # exactly when a container had run out of memory and needed it raised.
    from digdown.docker_adapter import DockerAdapter

    class Api:
        def containers(self, all=True):
            return [{"Id": "abc", "Names": ["/web"], "Labels": {}, "State": "exited"}]

        def inspect_container(self, cid):
            return {"Name": "/web", "State": {"Status": "exited", "ExitCode": 137},
                    "HostConfig": {"Memory": 33554432}, "Config": {}}

    class Client:
        api = Api()

    adapter = DockerAdapter.__new__(DockerAdapter)
    adapter._client = Client()
    adapter.project_names, adapter.exclude_names = [], []
    adapter.collect_resource_stats = True
    assert adapter.list_states()[0].mem_limit_bytes == 33554432
