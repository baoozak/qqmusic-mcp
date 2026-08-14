from __future__ import annotations

import json
from argparse import Namespace

import pytest

from qqmusic_organizer import cli
from qqmusic_organizer.qqmusic import AuthenticationError


def test_config_shapes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_command", lambda: "qqmusic-mcp")
    assert cli.config(Namespace(client="claude")) == 0
    claude = json.loads(capsys.readouterr().out)
    assert claude["mcpServers"]["qqmusic-mcp"] == {"command": "qqmusic-mcp", "args": ["stdio"]}

    assert cli.config(Namespace(client="vscode")) == 0
    vscode = json.loads(capsys.readouterr().out)
    assert vscode["servers"]["qqmusic-mcp"]["type"] == "stdio"


def test_command_finds_launcher_next_to_python(monkeypatch, tmp_path) -> None:
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    python = scripts / "python.exe"
    launcher = scripts / "qqmusic-mcp.exe"
    python.touch()
    launcher.touch()
    monkeypatch.setattr(cli.sys, "argv", ["qqmusic-mcp"])
    monkeypatch.setattr(cli.sys, "executable", str(python))

    assert cli._command() == str(launcher.resolve())


def test_start_requires_http_token(monkeypatch, capsys) -> None:
    monkeypatch.delenv("QQMUSIC_ORGANIZER_TOKEN", raising=False)
    result = cli.start(Namespace(port=8765, login_timeout=300, wait=0))
    assert result == 2
    assert "requires QQMUSIC_ORGANIZER_TOKEN" in capsys.readouterr().err


def test_login_reuses_valid_encrypted_session(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_cookie", lambda _: "uin=o123; qm_keyst=test")
    monkeypatch.setattr(cli, "_validate_session", lambda _: 3)
    monkeypatch.setattr(cli, "_capture_login_cookie", lambda _: (_ for _ in ()).throw(AssertionError()))

    assert cli.login(Namespace(login_timeout=600, force=False)) == 0
    assert "already valid" in capsys.readouterr().out


def test_login_validates_before_saving(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "load_cookie", lambda _: None)
    monkeypatch.setattr(cli, "_capture_login_cookie", lambda _: "uin=o123; qm_keyst=test")
    monkeypatch.setattr(cli, "_validate_session", lambda _: calls.append("validate") or 2)
    monkeypatch.setattr(cli, "save_cookie", lambda *_: calls.append("save"))

    assert cli.login(Namespace(login_timeout=600, force=False)) == 0
    assert calls == ["validate", "save"]
    assert "saved securely" in capsys.readouterr().out


def test_login_does_not_save_failed_session(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_cookie", lambda _: None)
    monkeypatch.setattr(cli, "_capture_login_cookie", lambda _: "uin=o123; qm_keyst=test")
    monkeypatch.setattr(
        cli,
        "_validate_session",
        lambda _: (_ for _ in ()).throw(AuthenticationError("expired")),
    )
    monkeypatch.setattr(cli, "save_cookie", lambda *_: pytest.fail("invalid session was saved"))

    assert cli.login(Namespace(login_timeout=600, force=False)) == 2
    assert "login failed" in capsys.readouterr().err


def test_setup_orders_login_install_and_doctor(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "login", lambda _: calls.append("login") or 0)
    monkeypatch.setattr(cli, "install", lambda _: calls.append("install") or 0)
    monkeypatch.setattr(cli, "doctor", lambda _: calls.append("doctor") or 0)

    args = Namespace(client="codex", login_timeout=600, force_login=False)
    assert cli.setup(args) == 0
    assert calls == ["login", "install", "doctor"]


def test_new_commands_parse() -> None:
    parser = cli.make_parser()
    assert parser.parse_args(["login"]).login_timeout == 600
    assert parser.parse_args(["setup", "--client", "cursor"]).client == "cursor"
    assert parser.parse_args(["doctor", "--client", "vscode"]).client == "vscode"


def test_codex_install_reports_missing_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)

    assert cli.install(Namespace(client="codex")) == 2
    assert "not on PATH" in capsys.readouterr().err


def test_codex_registration_must_match_current_launcher(monkeypatch, tmp_path) -> None:
    current = tmp_path / "qqmusic-mcp.exe"
    old = tmp_path / "qqmusic-organizer.exe"
    current.touch()
    old.touch()
    payload = {
        "transport": {"type": "stdio", "command": str(old), "args": ["stdio"]}
    }
    monkeypatch.setattr(cli.shutil, "which", lambda name: "codex.exe" if name == "codex" else None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_, **__: Namespace(returncode=0, stdout=json.dumps(payload)),
    )

    assert cli._codex_registration_status(str(current)) == "mismatch"
