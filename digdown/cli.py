"""The `digdown` command."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import webbrowser

import uvicorn

from . import __version__, checks
from .config import ConfigError

_MARK = {checks.OK: "ok  ", checks.WARN: "warn", checks.FAIL: "FAIL"}


def _url(host: str, port: int) -> str:
    shown = "localhost" if host in ("0.0.0.0", "127.0.0.1", "::", "::1") else host
    return f"http://{shown}:{port}"


def sign_in_link(host: str, port: int, token: str) -> str:
    """Opening this signs in. The token goes after the #, so the browser never sends
    it to a server, and the dashboard removes it from the address bar."""
    return f"{_url(host, port)}/#token={token}"


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _serve(app, host: str, port: int) -> int:
    if not _port_free(host, port):
        print(f"Port {port} is already in use. Stop whatever is using it, or choose "
              f"another port with DIGDOWN_PORT (for the demo, --port).")
        return 1
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def cmd_start(_args) -> int:
    from .main import build_app

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        app, config = build_app()
    except ConfigError as e:
        print(f"Digdown can't start.\n\n{e}\n\nRun `digdown doctor` for a full check.")
        return 2
    print(f"\nDigdown is running at {_url(config.host, config.port)}")
    if app.state.first_run:
        print("Open it in your browser to finish setup.")
    if app.state.generated_token:
        print("\nThis copy needs a token, because it can control Docker. "
              "Open this link to sign in:\n\n"
              f"  {sign_in_link(config.host, config.port, app.state.generated_token)}\n\n"
              f"Or paste the token: {app.state.generated_token}\n")
    print("Press Ctrl+C to stop.\n")
    return _serve(app, config.host, config.port)


def cmd_demo(args) -> int:
    from .demo import build_demo

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    demo = build_demo()
    url = _url("127.0.0.1", args.port)
    print(f"\nDigdown demo is running at {url}")
    print("The containers and the AI are simulated. Nothing on this computer changes,")
    print("and no data leaves it. Press Ctrl+C to stop and delete the demo data.\n")
    if not args.no_browser:
        webbrowser.open(url)
    # The server re-raises this after shutting down. Without a handler that ends the
    # process on the spot, and the cleanup below never runs.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        return _serve(demo.app, "127.0.0.1", args.port)
    finally:
        demo.cleanup()


def cmd_doctor(_args) -> int:
    results = checks.run_all()
    print()
    for c in results:
        print(f"  {_MARK[c.status]}  {c.name}: {c.detail}")
        if c.fix and c.status != checks.OK:
            print(f"        {c.fix}")
        if c.technical:
            print(f"        ({c.technical})")
    failed = sum(c.status == checks.FAIL for c in results)
    print(f"\n{failed} problem{'s' if failed != 1 else ''} to fix.\n" if failed
          else "\nNo problems found.\n")
    return 1 if failed else 0


def cli(argv=None) -> int:
    # Output to a pipe is buffered until exit, which in a container would hide the
    # startup message, including a generated token, from `docker logs`.
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(
        prog="digdown",
        description="Watches your Docker containers, explains why they failed, and "
                    "fixes them after you approve.")
    parser.add_argument("--version", action="version", version=f"digdown {__version__}")
    parser.add_argument("--config", help="settings file (default: ./digdown.yml)")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("start", help="watch your containers (the default)")
    demo = commands.add_parser("demo", help="try it with simulated containers, no setup")
    demo.add_argument("--port", type=int, default=8088)
    demo.add_argument("--no-browser", action="store_true", help="don't open a browser")
    commands.add_parser("doctor", help="check that everything is set up correctly")
    args = parser.parse_args(argv)
    if args.config:
        os.environ["DIGDOWN_CONFIG"] = args.config
    handler = {"demo": cmd_demo, "doctor": cmd_doctor}.get(args.command, cmd_start)
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(cli())
