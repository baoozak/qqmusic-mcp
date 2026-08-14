# Contributing

This project targets Windows 10/11 and Python 3.11 or newer.

```powershell
git clone https://github.com/baoozak/qqmusic-mcp.git
cd qqmusic-mcp
uv sync --extra dev
uv run pytest
uv build
```

Keep changes narrowly scoped. Add tests for protocol parsing, authentication,
write behavior, or CLI changes. Never commit Cookies, tokens, exported music
libraries, `%LOCALAPPDATA%\QQMusicOrganizer`, or browser profiles.

QQ Music write changes must preserve these invariants:

- Never remove a song from “我喜欢”.
- Never write to or delete `dirId=201`.
- Keep write probing, batching, read-after-write verification, and run logs.
- Redact authentication material from errors and logs.

Before opening a pull request, run `uv run pytest` and `uv build`.

