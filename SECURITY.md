# Security policy

Netwatch runs as the gateway and DNS server of people's home networks, so security problems matter.

## Reporting a vulnerability

**Please don't open a public issue.** Use GitHub's private reporting instead:
[Report a vulnerability](https://github.com/ahmedjanwar/netwatch-oss/security/advisories/new).

Include what an attacker could do, the steps to reproduce it, and your version (`sudo netwatch version`).
You'll get a reply within a week. Once a fix is out, you'll be credited in the release notes unless you'd
rather not be.

## Supported versions

Security fixes go into the **latest release**, published as a patch release as soon as possible. Update
with `sudo netwatch update`, and check what you run with `sudo netwatch version`.

## What's in scope

- Getting into the dashboard or its API without the password, or bypassing the first-run password change
- Reading or changing settings, devices or history without signing in
- Making Netwatch block, unblock or redirect devices without the owner's permission
- Leaking the dashboard password or the Telegram bot token
- The installer or `netwatch` command doing something unsafe on the host

Things that need physical or root access to the Netwatch machine are out of scope.
