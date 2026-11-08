import pytest

from digdown import cli as cli_module
from digdown.cli import _port_free, _url, cli


def test_urls_are_ones_a_browser_can_open():
    assert _url("0.0.0.0", 8088) == "http://localhost:8088"
    assert _url("127.0.0.1", 9000) == "http://localhost:9000"
    assert _url("ops.example.com", 8088) == "http://ops.example.com:8088"


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli(["--version"])
    assert e.value.code == 0
    assert "digdown " in capsys.readouterr().out


def test_bad_config_prints_advice_not_a_traceback(tmp_path, monkeypatch, capsys):
    p = tmp_path / "digdown.yml"
    p.write_text("mode: yolo\n")
    assert cli(["--config", str(p), "start"]) == 2
    out = capsys.readouterr().out
    assert "can't start" in out and "mode" in out and "doctor" in out
    assert "Traceback" not in out
    monkeypatch.delenv("DIGDOWN_CONFIG", raising=False)


def test_busy_port_is_explained(capsys):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert _port_free("127.0.0.1", port) is False
        assert cli_module._serve(None, "127.0.0.1", port) == 1
    assert "already in use" in capsys.readouterr().out


def test_doctor_exit_code_reflects_failures(monkeypatch, capsys):
    from digdown import checks
    monkeypatch.setattr(checks, "run_all",
                        lambda: [checks.Check("Docker", checks.FAIL, "down", "start it")])
    assert cli(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "start it" in out
    monkeypatch.setattr(checks, "run_all",
                        lambda: [checks.Check("Docker", checks.OK, "fine")])
    assert cli(["doctor"]) == 0


def test_startup_message_is_visible_when_output_is_piped(tmp_path):
    """`docker logs` reads a pipe. The token has to reach it while Digdown runs."""
    import os
    import subprocess
    import sys
    import time
    env = {**os.environ, "DIGDOWN_HOST": "0.0.0.0", "DIGDOWN_PORT": "0",
           "DIGDOWN_CONFIG": str(tmp_path / "digdown.yml"),
           "DIGDOWN_DB_PATH": str(tmp_path / "o.db"), "DOCKER_HOST": "unix:///nonexistent",
           "PYTHONPATH": os.getcwd(), "PYTHONUNBUFFERED": ""}
    p = subprocess.Popen([sys.executable, "-m", "digdown.cli", "start"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        lines = []
        deadline = time.time() + 30
        while time.time() < deadline and not (tmp_path / "digdown.token").exists():
            time.sleep(0.1)
        token = (tmp_path / "digdown.token").read_text().strip()
        while time.time() < deadline:
            line = p.stdout.readline()
            lines.append(line)
            if token in line:
                break
        assert any(token in line for line in lines)
    finally:
        p.terminate()
        p.wait(timeout=10)


def test_stopping_the_demo_deletes_its_data(tmp_path):
    """It promises this when it starts, and `kill` and `docker stop` send SIGTERM."""
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "TMPDIR": str(tmp_path), "PYTHONPATH": os.getcwd()}
    p = subprocess.Popen([sys.executable, "-m", "digdown.cli", "demo", "--no-browser",
                          "--port", str(port)], env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        assert list(tmp_path.glob("digdown-demo-*"))
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=15)
    finally:
        p.kill()
    assert list(tmp_path.glob("digdown-demo-*")) == []


def test_sign_in_link_carries_the_token_after_the_hash():
    from digdown.cli import sign_in_link
    link = sign_in_link("0.0.0.0", 8088, "abc123")
    assert link == "http://localhost:8088/#token=abc123"
