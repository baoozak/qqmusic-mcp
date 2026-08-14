from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import socket
import secrets
import shutil
import subprocess
import sys
import time
import signal
from pathlib import Path
from typing import Any

import uvicorn

from .organizer import Organizer
from .qqmusic import AuthenticationError, QQMusicClient, parse_cookie
from .server import build_mcp
from .session_cache import delete_cookie, load_cookie, save_cookie
from .storage import Storage, default_data_dir


SERVICE_FILE = default_data_dir() / "service.json"
SERVICE_STDOUT = default_data_dir() / "service.out.log"
SERVICE_STDERR = default_data_dir() / "service.err.log"


class StartupError(RuntimeError):
    pass


class BearerAuth:
    def __init__(self, app: object, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode("ascii")

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            if not secrets.compare_digest(headers.get(b"authorization", b""), self.expected):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def _create_organizer(args: argparse.Namespace) -> tuple[Organizer, QQMusicClient]:
    cache_path = default_data_dir() / "session.dpapi"
    raw_cookie: str | None = None
    if not args.manual_cookie:
        try:
            raw_cookie = load_cookie(cache_path)
        except (OSError, UnicodeDecodeError, RuntimeError, ValueError):
            delete_cookie(cache_path)
    if args.manual_cookie:
        raw_cookie = getpass.getpass("Paste QQ Music Cookie (hidden, memory only): ").strip()
    elif raw_cookie is None:
        from .browser_auth import capture_qqmusic_cookie

        try:
            raw_cookie = capture_qqmusic_cookie(args.login_timeout)
        except (RuntimeError, TimeoutError, ValueError) as error:
            raise StartupError(f"Automatic QQ Music login failed: {error}") from error
        save_cookie(cache_path, raw_cookie)
    try:
        parse_cookie(raw_cookie)
    except ValueError as error:
        raise StartupError(f"Invalid Cookie: {error}") from error
    client = QQMusicClient(raw_cookie)
    if not args.manual_cookie and cache_path.exists():
        try:
            asyncio.run(client.list_created_playlists())
        except AuthenticationError:
            asyncio.run(client.close())
            delete_cookie(cache_path)
            from .browser_auth import capture_qqmusic_cookie

            raw_cookie = capture_qqmusic_cookie(args.login_timeout)
            save_cookie(cache_path, raw_cookie)
            client = QQMusicClient(raw_cookie)
    storage = Storage()
    storage.start_session()
    return Organizer(client, storage), client


def serve(args: argparse.Namespace) -> int:
    token = os.environ.get("QQMUSIC_ORGANIZER_TOKEN", "")
    if len(token) < 32:
        print("Set QQMUSIC_ORGANIZER_TOKEN to a random value of at least 32 characters.", file=sys.stderr)
        print("Generate one with: qqmusic-mcp token", file=sys.stderr)
        return 2
    try:
        organizer, client = _create_organizer(args)
    except StartupError as error:
        print(error, file=sys.stderr)
        return 2
    mcp = build_mcp(organizer)
    app = BearerAuth(mcp.streamable_http_app(), token)
    print(f"QQ Music Organizer MCP: http://127.0.0.1:{args.port}/mcp")
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        asyncio.run(client.close())
    return 0


def stdio(args: argparse.Namespace) -> int:
    try:
        organizer, client = _create_organizer(args)
    except StartupError as error:
        print(error, file=sys.stderr)
        return 2
    try:
        build_mcp(organizer).run(transport="stdio")
    finally:
        asyncio.run(client.close())
    return 0


def _command() -> str:
    executable = Path(sys.argv[0]).resolve()
    if executable.exists() and executable.suffix.casefold() == ".exe":
        return str(executable)
    launcher = Path(sys.executable).resolve().with_name("qqmusic-mcp.exe")
    if launcher.exists():
        return str(launcher)
    return shutil.which("qqmusic-mcp") or "qqmusic-mcp"


def _is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _read_service() -> dict[str, Any] | None:
    try:
        data = json.loads(SERVICE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_path(pid: int) -> Path | None:
    if os.name != "nt" or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value).resolve()
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def start(args: argparse.Namespace) -> int:
    state = _read_service()
    if state and _process_exists(int(state.get("pid", 0))) and _is_port_open(int(state.get("port", 8765))):
        print(f"QQ Music Organizer is already running (PID {state['pid']}).")
        return 0
    token = os.environ.get("QQMUSIC_ORGANIZER_TOKEN", "")
    if len(token) < 32:
        print("HTTP mode requires QQMUSIC_ORGANIZER_TOKEN (at least 32 characters).", file=sys.stderr)
        print("Use 'qqmusic-mcp token' to generate one, or use the recommended stdio installation.", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["QQMUSIC_ORGANIZER_TOKEN"] = token
    default_data_dir().mkdir(parents=True, exist_ok=True)
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
    with SERVICE_STDOUT.open("a", encoding="utf-8") as stdout, SERVICE_STDERR.open("a", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [_command(), "serve", "--port", str(args.port), "--login-timeout", str(args.login_timeout)],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=env,
            creationflags=creation_flags,
        )
    Storage._write_json(SERVICE_FILE, {"pid": process.pid, "port": args.port, "command": _command()})
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        if _is_port_open(args.port):
            print(f"QQ Music Organizer started: http://127.0.0.1:{args.port}/mcp (PID {process.pid})")
            return 0
        if process.poll() is not None:
            print(f"Service exited. See {SERVICE_STDERR}", file=sys.stderr)
            return 1
        time.sleep(0.5)
    print("Service is waiting for browser login; complete login in the opened window.")
    return 0


def status(_: argparse.Namespace) -> int:
    state = _read_service()
    if not state:
        print("QQ Music Organizer is not managed by the CLI.")
        return 1
    pid, port = int(state.get("pid", 0)), int(state.get("port", 8765))
    running = _process_exists(pid)
    ready = running and _is_port_open(port)
    print(json.dumps({"running": running, "ready": ready, "pid": pid, "url": f"http://127.0.0.1:{port}/mcp"}, indent=2))
    return 0 if running else 1


def stop(_: argparse.Namespace) -> int:
    state = _read_service()
    if not state:
        print("QQ Music Organizer is not running.")
        return 0
    pid = int(state.get("pid", 0))
    if _process_exists(pid):
        expected = Path(str(state.get("command", ""))).resolve()
        actual = _process_path(pid)
        if actual is None or actual != expected:
            print("Refusing to stop a process that is not QQ Music Organizer.", file=sys.stderr)
            return 2
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while _process_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
    SERVICE_FILE.unlink(missing_ok=True)
    print("QQ Music Organizer stopped.")
    return 0


def logout(_: argparse.Namespace) -> int:
    delete_cookie(default_data_dir() / "session.dpapi")
    print("Encrypted QQ Music login cache removed.")
    return 0


def config(args: argparse.Namespace) -> int:
    command = _command()
    server = {"command": command, "args": ["stdio"]}
    if args.client == "vscode":
        payload = {"servers": {"qqmusic-mcp": {"type": "stdio", **server}}}
    else:
        payload = {"mcpServers": {"qqmusic-mcp": server}}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def install(args: argparse.Namespace) -> int:
    if args.client != "codex":
        config(argparse.Namespace(client=args.client))
        print("Add the configuration above to your MCP client.", file=sys.stderr)
        return 0
    for name in ("qqmusic-organizer", "qqmusic-mcp"):
        subprocess.run(["codex", "mcp", "remove", name], capture_output=True, check=False)
    result = subprocess.run(["codex", "mcp", "add", "qqmusic-mcp", "--", _command(), "stdio"])
    if result.returncode:
        print("Could not register Codex. Is codex available on PATH?", file=sys.stderr)
        return result.returncode
    print("Registered qqmusic-mcp with Codex using standard MCP stdio transport.")
    return 0


def uninstall(args: argparse.Namespace) -> int:
    stop_result = stop(args)
    if stop_result:
        return stop_result
    for name in ("qqmusic-organizer", "qqmusic-mcp"):
        subprocess.run(["codex", "mcp", "remove", name], capture_output=True, check=False)
    if args.purge:
        logout(args)
        print(f"Exports and run history remain in {default_data_dir()}.")
    print("Removed the Codex MCP registration.")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qqmusic-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    token_parser = subparsers.add_parser("token", help="generate a local MCP bearer token")
    token_parser.set_defaults(func=lambda _: print(secrets.token_urlsafe(32)) or 0)
    serve_parser = subparsers.add_parser("serve", help="start the localhost MCP server")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--login-timeout", type=int, default=300, help="browser login timeout in seconds")
    serve_parser.add_argument("--manual-cookie", action="store_true", help="use hidden terminal Cookie input instead")
    serve_parser.set_defaults(func=serve)
    stdio_parser = subparsers.add_parser("stdio", help="run as a standard MCP stdio server")
    stdio_parser.add_argument("--login-timeout", type=int, default=300)
    stdio_parser.add_argument("--manual-cookie", action="store_true")
    stdio_parser.set_defaults(func=stdio)
    start_parser = subparsers.add_parser("start", help="start the HTTP server in the background")
    start_parser.add_argument("--port", type=int, default=8765)
    start_parser.add_argument("--login-timeout", type=int, default=300)
    start_parser.add_argument("--wait", type=int, default=10, help="seconds to wait for readiness")
    start_parser.set_defaults(func=start)
    subparsers.add_parser("status", help="show background service status").set_defaults(func=status)
    subparsers.add_parser("stop", help="stop the managed background service").set_defaults(func=stop)
    subparsers.add_parser("logout", help="remove the encrypted QQ Music login cache").set_defaults(func=logout)
    install_parser = subparsers.add_parser("install", help="register this MCP with a client")
    install_parser.add_argument("--client", choices=["codex", "claude", "cursor", "vscode"], default="codex")
    install_parser.set_defaults(func=install)
    config_parser = subparsers.add_parser("config", help="print MCP client configuration JSON")
    config_parser.add_argument("--client", choices=["claude", "cursor", "vscode"], default="claude")
    config_parser.set_defaults(func=config)
    uninstall_parser = subparsers.add_parser("uninstall", help="stop the service and remove Codex registration")
    uninstall_parser.add_argument("--purge", action="store_true", help="also remove the encrypted login cache")
    uninstall_parser.set_defaults(func=uninstall)
    return parser


def main() -> None:
    args = make_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
