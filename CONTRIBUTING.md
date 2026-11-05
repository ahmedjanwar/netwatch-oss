# Contributing to Netwatch

Thanks for helping! Bug reports, ideas, docs fixes and code are all welcome.

## Reporting a bug

[Open an issue](https://github.com/ahmedjanwar/netwatch-oss/issues/new/choose) and include:
- what you did, what you expected, and what happened;
- `sudo netwatch status` and the relevant part of `sudo netwatch logs`;
- your OS, your router's brand, and whether you're in monitor-only or gateway mode.

**Don't paste passwords, Telegram bot tokens, or a full device list from your home.** Logs can contain
device names and the websites they visit; trim them to what's needed.

Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Suggesting a feature

Open an issue describing what you were trying to do and what got in the way. Netwatch is used by people
who aren't network engineers, so ideas that make it clearer for them are especially welcome.

## Making a change

### Project layout

```
install.sh           the one-line installer
scripts/netwatch     the `netwatch` command (status, update, gateway on/off…)
gateway/app/         the Python app (FastAPI): engine, DHCP/DNS, firewall, Telegram, insights
gateway/web/         the dashboard (plain HTML, CSS and JavaScript, no build step)
tests/               the tests (pytest), run by CI on every push and pull request
lab/                 a test VM with a fake home network
```

### Try it safely in the lab

Netwatch changes how a network works, so **don't test changes on your home network.** The [`lab/`](lab/)
folder builds an Ubuntu VM (UTM on macOS) with a fake home network inside: a router, a phone, a TV and a
laptop. Run the real installer in it with:

```sh
sudo NETWATCH_SRC=/path/to/your/netwatch-oss bash /path/to/your/netwatch-oss/install.sh
```

See [lab/README.md](lab/README.md) for building the VM.

### Tests

The tests live in [`tests/`](tests/) and run with [pytest](https://docs.pytest.org). CI runs every file
in that folder on each push and pull request, so **a test you add is checked automatically from then on.**

Run them yourself in Netwatch's own image (Python 3.13, nothing to install on your machine):

```sh
docker compose build
docker run --rm -v "$PWD:/src" -w /src netwatch:latest \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest tests"
```

Or with Python 3.13 locally: `pip install -r gateway/requirements.txt -r requirements-dev.txt && pytest tests`.

To add a test, create `tests/test_<something>.py` with functions named `test_...`. Write test names as
plain sentences, like the existing ones (`test_dhcp_skips_tiny_leftover_ranges`).
[`conftest.py`](tests/conftest.py) points Netwatch at a throwaway data folder and a fake home network on
the loopback interface, so tests never touch a real network. For the web app, see
[`test_first_run.py`](tests/test_first_run.py), which signs in and walks the whole first-run flow.

**Fixing a bug? Add a test that fails without your fix.**

### Before you open a pull request

1. Branch from `main` (`fix/123-short-name` or `feat/short-name`) and link the issue.
2. Keep each pull request to one change.
3. Run the tests (above) and the self-test, which CI also runs:
   ```sh
   docker compose build && docker run --rm --network none --cap-add NET_ADMIN netwatch:latest python -m app.selftest
   ```
4. If you changed something people will notice, add a line under *Unreleased* in [CHANGELOG.md](CHANGELOG.md).
5. For dashboard changes, add a before/after screenshot to the pull request.

### Guidelines

- **Safe by default.** Netwatch must never change someone's network without them choosing to. New features
  that touch DHCP, DNS or the firewall should be off by default, and fail without taking the internet down.
- **Plain language.** Anything shown in the dashboard should make sense to someone who isn't technical.
- **No secrets in logs.** Never log passwords or tokens, and keep device data on the user's machine.
- **Few dependencies.** The dashboard has no build step; please keep it that way.

## Releases

`main` is where changes land. Users on the default **stable** channel only get **releases**, so `main`
can move faster than what people run at home.

- In your pull request, describe the change under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).
  That text becomes the release notes.
- To release (maintainers): **Actions → Release → Run workflow**, then pick the kind of release:
  - **patch** (0.1.0 → 0.1.1): bug fixes
  - **minor** (0.1.0 → 0.2.0): new features
  - **major** (0.1.0 → 1.0.0): changes to how people install or use Netwatch

  The workflow runs all of CI first (lint, tests, self-test, a real install). Then it bumps the version
  in `gateway/app/version.py`, turns *Unreleased* into the new version's section, tags `vX.Y.Z` and
  publishes the GitHub Release. Installs pick it up with `sudo netwatch update`.
- To preview the notes locally: `python3 scripts/release.py patch --notes /tmp/notes.md` (then
  `git checkout -- CHANGELOG.md gateway/app/version.py` to undo).

## Code of conduct

Be kind and assume good intent. This project follows the
[Contributor Covenant, version 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/):
see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
