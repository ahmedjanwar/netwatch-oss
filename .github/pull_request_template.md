## What this changes

<!-- What does it do, and why? Link the issue: "Fixes #123". -->

## How I tested it

<!-- In the lab VM, on a real network, or both? Monitor-only, gateway mode, or both? -->

## Checklist

- [ ] The tests pass, and I added a test for a bug fix or new behaviour (see CONTRIBUTING.md)
- [ ] The self-test passes (`docker run --rm --network none --cap-add NET_ADMIN netwatch:latest python -m app.selftest`)
- [ ] Nothing changes someone's network unless they choose it (safe by default)
- [ ] Anything shown in the dashboard is in plain language
- [ ] No passwords, tokens or personal network details in code, logs or screenshots
- [ ] `CHANGELOG.md` updated, if people will notice the change
- [ ] Screenshots attached, for dashboard changes
