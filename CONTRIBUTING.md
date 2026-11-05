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

### Before you open a pull request

1. Branch from `main` (`fix/123-short-name` or `feat/short-name`) and link the issue.
2. Keep each pull request to one change.
3. Run the self-test, which CI also runs:
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

## Code of conduct

Be kind and assume good intent. This project follows the
[Contributor Covenant, version 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/):
see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
