# Security Policy

## Supported versions

Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a vulnerability involving authentication,
Cookie handling, arbitrary file access, or unintended QQ Music writes. Use the
repository owner's private security-reporting channel instead. Include the
affected version, reproduction steps, and expected impact. Never include a
real QQ Music Cookie or MCP bearer token.

## Local security model

- The MCP binds HTTP mode to `127.0.0.1` only and requires a bearer token.
- Standard stdio mode does not expose a network port.
- QQ Music login Cookies are encrypted with Windows DPAPI for the current user.
- Cookies and bearer tokens are excluded from logs and MCP responses.
- A write probe must succeed before a plan can be applied.
- Finalized plans are integrity checked with SHA-256.
- The liked playlist (`dirId=201`) is never a write or delete target.
- Writes use batches of at most 20 songs and are read back for verification.
- Generic playlist writes create a local operation log; deleting a playlist also
  requires it to be empty and is confirmed by a second playlist read.

This project uses unofficial QQ Music web APIs. Treat every upstream response
as untrusted and stop writes if its shape changes.
