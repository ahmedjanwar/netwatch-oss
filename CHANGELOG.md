# Changelog

All notable changes to Netwatch. Versions follow [semantic versioning](https://semver.org): a **patch**
release fixes things, a **minor** release adds features, a **major** release changes how you use it.

Add your change under **Unreleased** in the same pull request; the release workflow turns that section
into the next version's release notes.

## Unreleased

## 0.1.0 (2026-11-05)

First public release.

- **One-line installer** for Ubuntu and Debian: sets up Docker, installs the latest release, starts in
  monitor-only mode, and prints the dashboard address and a temporary password.
- **First-run setup:** choose your own password, see what Netwatch does, confirm the detected network
  and router, and pick features.
- **The `netwatch` command:** status, version, password reset, fixed IP, gateway mode, update with a
  self-test and automatic rollback, release channels, and uninstall.
- **Releases and channels:** `sudo netwatch update` installs the latest release (the *stable* channel).
  Testers can follow `main` with `sudo netwatch channel edge`.
- Live device map, new-device approval, block/pause/full speed, whole-network ad blocking, insights and
  a Telegram bot.
- **Tests:** a pytest suite in `tests/`, run by CI on every push and pull request, plus a test that
  installs Netwatch on a fresh Ubuntu machine.
