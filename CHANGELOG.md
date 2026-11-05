# Changelog

## Unreleased

- **Tests:** a pytest suite in `tests/` (configuration, passwords, router names, traffic parsing, the
  whole first-run flow through the web app), run by CI on every push and pull request.

## 0.1.0

First public release.

- **One-line installer** for Ubuntu and Debian: sets up Docker, starts in monitor-only mode, prints the
  dashboard address and a temporary password.
- **First-run setup:** choose your own password, see what Netwatch does, confirm the detected network
  and router, and pick features.
- **The `netwatch` command:** status, password reset, fixed IP, gateway mode, update with automatic
  rollback, and uninstall.
- Live device map, new-device approval, block/pause/full speed, whole-network ad blocking, insights and
  a Telegram bot.
