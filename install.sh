#!/usr/bin/env bash
# Netwatch installer.
#
#   curl -fsSL https://raw.githubusercontent.com/ahmedjanwar/netwatch-oss/main/install.sh | sudo bash
#
# Installs Docker if needed, puts Netwatch in /opt/netwatch, starts it in monitor-only mode
# (it never changes your network on its own) and prints the address and a temporary password.
# Options (environment variables):
#   NETWATCH_DIR=/opt/netwatch     where to install
#   NETWATCH_PORT=8088             dashboard port
#   NETWATCH_CHANNEL=stable        stable = the latest release (default); edge = the latest code on main
#   NETWATCH_REPO=<git url>        install from another repository or fork
#   NETWATCH_REF=v0.1.0            install a specific release, tag or branch
#   NETWATCH_SRC=/path/to/netwatch install from a local copy instead of downloading (for testing)
set -euo pipefail

DIR=${NETWATCH_DIR:-/opt/netwatch}
PORT=${NETWATCH_PORT:-8088}
REPO=${NETWATCH_REPO:-https://github.com/ahmedjanwar/netwatch-oss.git}
CHANNEL=${NETWATCH_CHANNEL:-stable}
REF=${NETWATCH_REF:-}

bold=$(printf '\033[1m'); dim=$(printf '\033[2m'); green=$(printf '\033[32m'); red=$(printf '\033[31m'); off=$(printf '\033[0m')
say()  { printf '%s\n' "${bold}==>${off} $*"; }
fail() { printf '%s\n' "${red}✗ $*${off}" >&2; exit 1; }

# ---------- checks ----------
[ "$(id -u)" = 0 ] || fail "Run the installer with sudo:  curl -fsSL … | sudo bash"
[ "$(uname -s)" = Linux ] || fail "Netwatch runs on Linux (Ubuntu or Debian). It manages this machine's network, so it can't run on macOS or Windows."
. /etc/os-release 2>/dev/null || fail "Couldn't tell which Linux this is (no /etc/os-release)."
case "$CHANNEL" in stable|edge) ;; *) fail "NETWATCH_CHANNEL must be stable or edge" ;; esac
case " ${ID:-} ${ID_LIKE:-} " in
  *" ubuntu "*|*" debian "*) ;;
  *) fail "Netwatch supports Ubuntu and Debian (and their derivatives). This is ${PRETTY_NAME:-unknown}." ;;
esac
command -v systemctl >/dev/null || fail "systemd is required."
if [ -f "$DIR/docker-compose.yml" ]; then
  fail "Netwatch is already installed in $DIR. Use:  sudo netwatch update   (or  sudo netwatch uninstall  first)"
fi
ip -4 route show default | grep -q . || fail "This machine has no internet route. Connect it to your home network first."
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; then
  fail "Port $PORT is already in use. Pick another one:  … | sudo NETWATCH_PORT=8090 bash"
fi

say "Installing Netwatch on ${PRETTY_NAME}"

# ---------- dependencies ----------
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1
need=()
command -v docker >/dev/null || need+=(docker.io)
command -v git >/dev/null || [ -n "${NETWATCH_SRC:-}" ] || need+=(git)
command -v curl >/dev/null || need+=(curl)
docker buildx version >/dev/null 2>&1 || need+=(docker-buildx)
if [ ${#need[@]} -gt 0 ] || ! docker compose version >/dev/null 2>&1; then
  say "Installing ${need[*]:-} Docker Compose ${dim}(from your distribution's packages)${off}"
  apt-get update -qq
  [ ${#need[@]} -gt 0 ] && apt-get install -y -qq "${need[@]}" >/dev/null
  if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 \
      || apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 \
      || fail "Couldn't install Docker Compose v2. Install it (https://docs.docker.com/compose/install/) and run this again."
  fi
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker info >/dev/null 2>&1 || fail "Docker isn't running (try: sudo systemctl start docker)."

# ---------- get Netwatch ----------
if [ -n "${NETWATCH_SRC:-}" ]; then
  say "Copying Netwatch from $NETWATCH_SRC"
  mkdir -p "$DIR"
  (cd "$NETWATCH_SRC" && tar --exclude=./data --exclude=./.env --exclude=./.git -cf - .) | tar -xf - -C "$DIR"
else
  if [ -z "$REF" ] && [ "$CHANNEL" = stable ]; then
    REF=$(git ls-remote --tags --refs "$REPO" 'v*' | sed 's#.*refs/tags/##' \
          | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1 || true)
    [ -n "$REF" ] || { say "No release yet, installing the latest code on main"; CHANNEL=edge; }
  fi
  REF=${REF:-main}
  say "Downloading Netwatch $REF"
  git -c advice.detachedHead=false clone -q --depth 1 --branch "$REF" "$REPO" "$DIR"
fi
cd "$DIR"
mkdir -p data && chmod 700 data
[ -n "${NETWATCH_SRC:-}" ] || echo "$CHANNEL" > data/channel
[ -f .env ] || printf '# Netwatch settings. Most are set in the dashboard; see .env.example for the rest.\nNW_PORT=%s\n' "$PORT" > .env
chmod 600 .env

# ---------- build, test, start ----------
say "Building ${dim}(takes a minute the first time)${off}"
docker compose build -q
say "Running the self-test ${dim}(in a sandbox, without touching your network)${off}"
docker run --rm --network none --cap-add NET_ADMIN netwatch:latest python -m app.selftest >/dev/null 2>&1 \
  || fail "The self-test failed, so Netwatch was not started. Please open an issue with the output of:  docker run --rm --network none --cap-add NET_ADMIN netwatch:latest python -m app.selftest"
docker compose up -d >/dev/null

ln -sf "$DIR/scripts/netwatch" /usr/local/bin/netwatch
chmod +x "$DIR/scripts/netwatch"

say "Starting"
for _ in $(seq 1 30); do
  [ -s data/initial-password ] && curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null && break
  sleep 1
done
[ -s data/initial-password ] || fail "Netwatch didn't start. See:  sudo netwatch logs"

IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src"){print $(i+1); exit}}')
PW=$(cat data/initial-password)
cat <<EOF

${green}${bold}✓ Netwatch $(sed -n 's/^__version__ = "\(.*\)"/\1/p' gateway/app/version.py) is running${off}  ${dim}(monitor-only: your network is unchanged)${off}

  Open the dashboard:   ${bold}http://${IP:-this-machine}:$PORT${off}
  Temporary password:   ${bold}$PW${off}

  You'll choose your own password and a few settings on first sign-in.

  ${dim}Lost the password?   sudo netwatch password
  Status and logs:      sudo netwatch status | sudo netwatch logs
  Update / remove:      sudo netwatch update | sudo netwatch uninstall${off}

EOF
