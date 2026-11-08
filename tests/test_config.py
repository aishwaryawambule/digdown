from digdown.config import Config, load_config


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(str(tmp_path / "missing.yml"))
    assert cfg.mode == "observe"
    assert cfg.poll_interval_seconds == 15
    assert cfg.restart_loop_threshold == 3
    assert cfg.disk_percent_threshold == 90.0


def test_loads_overrides_from_yaml(tmp_path):
    p = tmp_path / "digdown.yml"
    p.write_text("mode: observe\npoll_interval_seconds: 5\nrestart_loop_threshold: 2\n")
    cfg = load_config(str(p))
    assert cfg.poll_interval_seconds == 5
    assert cfg.restart_loop_threshold == 2
    assert cfg.disk_percent_threshold == 90.0  # untouched default


def test_agent_defaults():
    cfg = Config()
    assert cfg.agent_enabled is True
    assert cfg.max_critic_rounds == 2
    assert cfg.max_tool_calls == 8
    assert cfg.llm_base_url.endswith("/v1")


def test_project_name_empty_default():
    assert Config().project_name == []


def test_project_name_accepts_single_string():
    # Back-compat: a bare string still scopes to one project.
    assert Config(project_name="shop").project_name == ["shop"]


def test_project_name_accepts_list():
    cfg = Config(project_name=["shop", "grafana"])
    assert cfg.project_name == ["shop", "grafana"]


def test_loads_multiple_projects_from_yaml(tmp_path):
    p = tmp_path / "digdown.yml"
    p.write_text("project_name:\n  - shop\n  - grafana\n")
    cfg = load_config(str(p))
    assert cfg.project_name == ["shop", "grafana"]


def test_probes_http_parses(tmp_path):
    from digdown.config import load_config
    p = tmp_path / "digdown.yml"
    p.write_text(
        "probes:\n"
        "  http:\n"
        "    - name: home\n"
        "      url: http://127.0.0.1:8080/healthz\n"
        "      target: web\n"
        "      latency_threshold_ms: 1500\n"
    )
    cfg = load_config(str(p))
    assert len(cfg.probes.http) == 1
    assert cfg.probes.http[0].name == "home"
    assert cfg.probes.http[0].target == "web"
    assert cfg.probes.http[0].latency_threshold_ms == 1500


def test_probes_default_empty():
    from digdown.config import Config
    assert Config().probes.http == []


def test_probes_logs_parses(tmp_path):
    from digdown.config import load_config
    p = tmp_path / "digdown.yml"
    p.write_text(
        "probes:\n"
        "  logs:\n"
        "    - target: shop-nginx\n"
        "      name: 5xx\n"
        "      threshold: 3\n"
        "      window_seconds: 30\n"
    )
    cfg = load_config(str(p))
    assert len(cfg.probes.logs) == 1
    assert cfg.probes.logs[0].target == "shop-nginx"
    assert cfg.probes.logs[0].name == "5xx"
    assert cfg.probes.logs[0].threshold == 3
    assert cfg.probes.logs[0].patterns == []   # default → probe fills defaults


def test_probes_logs_default_empty():
    from digdown.config import Config
    assert Config().probes.logs == []


from digdown.config import (Config, RUNTIME_SAFE_KEYS, SECRET_KEYS,
                                  apply_overrides, config_view)


def test_apply_overrides_applies_whitelisted_only():
    cfg = Config()
    applied = apply_overrides(cfg, {
        "mode": "execute",            # whitelisted
        "verify_polls": 9,            # whitelisted
        "compose_path": "/etc/x.yml", # NOT whitelisted -> ignored
        "bogus_key": 1,               # unknown -> ignored
    })
    assert set(applied) == {"mode", "verify_polls"}
    assert cfg.mode == "execute"
    assert cfg.verify_polls == 9
    assert cfg.compose_path == "docker-compose.yml"  # untouched


def test_apply_overrides_skips_invalid_value():
    cfg = Config()
    applied = apply_overrides(cfg, {"verify_polls": "not-an-int-???"})
    # pydantic can't coerce -> skipped, config unchanged
    assert "verify_polls" not in applied
    assert cfg.verify_polls == 6


def test_config_view_annotates_and_redacts_secrets():
    cfg = Config(auth_token="s3cret", mode="propose")
    view = config_view(cfg, overrides_meta={"mode": {"identity": "a", "updated_at": "t"}})
    assert view["mode"] == "propose"
    fields = {f["key"]: f for f in view["fields"]}
    # secret redacted to set/unset, never the value, not editable
    assert fields["auth_token"]["value"] == "set"
    assert fields["auth_token"]["secret"] is True
    assert fields["auth_token"]["editable"] is False
    assert "s3cret" not in str(view)
    # editable whitelisted key, source reflects the override
    assert fields["mode"]["editable"] is True
    assert fields["mode"]["source"] == "override"
    assert fields["poll_interval_seconds"]["source"] == "config"
    # demoted key is read-only + requires_restart
    assert fields["exclude_projects"]["editable"] is False
    assert fields["exclude_projects"]["requires_restart"] is True


def test_overrides_layer_onto_yaml_at_startup(tmp_path):
    # Simulate build_app's startup layering without Docker: load defaults, then
    # apply persisted overrides from a store.
    from digdown.store import Store
    store = Store(str(tmp_path / "t.db"))
    store.set_setting("mode", "propose")
    store.set_setting("poll_interval_seconds", 30)
    cfg = Config()                      # stands in for load_config()
    apply_overrides(cfg, store.list_settings())
    assert cfg.mode == "propose"
    assert cfg.poll_interval_seconds == 30


def test_mem_percent_threshold_is_runtime_editable():
    from digdown.config import RUNTIME_SAFE_KEYS
    assert "mem_percent_threshold" in RUNTIME_SAFE_KEYS
    assert "collect_resource_stats" not in RUNTIME_SAFE_KEYS  # startup-only
    assert Config().mem_percent_threshold == 90.0


def test_host_introspection_off_by_default_and_not_runtime_editable():
    from digdown.config import RUNTIME_SAFE_KEYS
    assert Config().host_introspection is False
    assert "host_introspection" not in RUNTIME_SAFE_KEYS
