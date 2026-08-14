from __future__ import annotations

import json
from argparse import Namespace

from qqmusic_organizer import cli


def test_config_shapes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_command", lambda: "qqmusic-mcp")
    assert cli.config(Namespace(client="claude")) == 0
    claude = json.loads(capsys.readouterr().out)
    assert claude["mcpServers"]["qqmusic-mcp"] == {"command": "qqmusic-mcp", "args": ["stdio"]}

    assert cli.config(Namespace(client="vscode")) == 0
    vscode = json.loads(capsys.readouterr().out)
    assert vscode["servers"]["qqmusic-mcp"]["type"] == "stdio"


def test_start_requires_http_token(monkeypatch, capsys) -> None:
    monkeypatch.delenv("QQMUSIC_ORGANIZER_TOKEN", raising=False)
    result = cli.start(Namespace(port=8765, login_timeout=300, wait=0))
    assert result == 2
    assert "requires QQMUSIC_ORGANIZER_TOKEN" in capsys.readouterr().err
