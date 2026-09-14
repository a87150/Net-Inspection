#!/usr/bin/env bash
# Production deployment for systemd Linux. Run from a stable checkout, e.g. /opt/net-inspection.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
ENV_FILE="$ROOT/.env"
PYTHON="python3"
SERVICE_USER="net-inspection"
INSTALL_PACKAGES=1
NON_INTERACTIVE=0
PREPARE_ONLY=0
usage() {
    echo "Usage: sudo bash deploy/linux/install.sh [--env-file /path/.env] [--python python3.12] [--skip-system-packages] [--non-interactive] [--prepare-only]"
}
while (($#)); do
    case "$1" in
        --env-file|--python)
            (($# >= 2)) || { usage; exit 2; }
            if [[ "$1" == --env-file ]]; then ENV_FILE="$2"; else PYTHON="$2"; fi
            shift 2 ;;
        --skip-system-packages) INSTALL_PACKAGES=0; shift ;;
        --non-interactive) NON_INTERACTIVE=1; shift ;;
        --prepare-only) PREPARE_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done
[[ $EUID == 0 ]] || { echo 'Run with sudo/root.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'This script requires systemd; containers/WSL without systemd are unsupported.' >&2; exit 1; }
ENV_FILE="$(realpath -m -- "$ENV_FILE")"
if [[ "$ROOT$ENV_FILE" =~ [\"\%\$\\] || "$ROOT$ENV_FILE" == *$'\n'* ]]; then
    echo 'Deployment paths must not contain quotes, backslashes, $, % or newlines.' >&2; exit 1
fi
cd -- "$ROOT"
exec 9>/run/lock/net-inspection-deploy.lock
flock -n 9 || { echo 'Another installation is running.' >&2; exit 1; }
OWNER="# Managed by NetInspection installer: $ROOT"
UNITS=(network-inspection-web.service network-inspection-worker.service)
for unit in "${UNITS[@]}"; do
    existing="$(systemctl show "$unit" --property=FragmentPath --value)"
    if [[ -n "$existing" ]] && ! grep -Fxq -- "$OWNER" "$existing"; then
        echo "Existing unit $unit belongs to another/manual deployment. Resolve it before installing." >&2; exit 1
    fi
done
# Only stop units owned by this installer; never kill an unrelated process.
for unit in network-inspection-worker.service network-inspection-web.service; do
    if [[ -n "$(systemctl show "$unit" --property=FragmentPath --value)" ]]; then systemctl disable "$unit"; systemctl stop "$unit"; fi
done
if ((INSTALL_PACKAGES)); then
    if command -v apt-get >/dev/null; then
        apt-get update
        apt-get install -y python3 python3-venv python3-dev build-essential pkg-config default-libmysqlclient-dev iputils-ping
    elif command -v dnf >/dev/null; then
        dnf install -y python3.12 python3.12-devel python3.12-pip gcc pkgconf-pkg-config mariadb-connector-c-devel iputils
        [[ "$PYTHON" != python3 ]] || PYTHON=python3.12
    else
        echo 'Install Python 3.12+, venv, development headers, pkg-config, MariaDB client headers and ping; rerun with --skip-system-packages.' >&2
        exit 1
    fi
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required (Ubuntu 24.04+/Debian 13+, or use --python)."'
"$PYTHON" -m deploy.setup processes
if [[ ! -x .venv/bin/python ]]; then "$PYTHON" -m venv .venv; fi
.venv/bin/python -c 'import sys; assert sys.version_info >= (3,12), "Existing .venv is too old; choose a fresh checkout/compatible environment."'
.venv/bin/python -m pip install -r requirements.lock.txt
ARGS=(--env-file "$ENV_FILE")
((NON_INTERACTIVE == 0)) || ARGS+=(--non-interactive)
.venv/bin/python -m deploy.setup configure "${ARGS[@]}"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "$SERVICE_USER"
fi
[[ "$(id -u "$SERVICE_USER")" != 0 ]] || { echo "Refusing a service account with UID 0." >&2; exit 1; }
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
chown root:"$SERVICE_GROUP" "$ENV_FILE"
chmod 640 "$ENV_FILE"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$ROOT/runtime/deployment" "$ROOT/runtime/deployment/logs" "$ROOT/runtime/deployment/staticfiles"
# Run setup as the real service user: validates file/key/DB access before enabling services.
runuser -u "$SERVICE_USER" -- "$ROOT/.venv/bin/python" -m deploy.setup preflight "${ARGS[@]}"
runuser -u "$SERVICE_USER" -- "$ROOT/.venv/bin/python" -m deploy.setup prepare "${ARGS[@]}"
if ((PREPARE_ONLY)); then echo 'Prepared. No systemd units started.'; exit 0; fi
for role in web worker; do
    unit="/etc/systemd/system/network-inspection-$role.service"
    kill_signal=SIGTERM
    [[ "$role" != web ]] || kill_signal=SIGINT
    cat >"$unit" <<EOF
$OWNER
[Unit]
Description=Network Inspection $role
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory="$ROOT"
Environment=PYTHONUTF8=1
Environment=PYTHONUNBUFFERED=1
ExecStart="$ROOT/.venv/bin/python" -u -m deploy.service $role --env-file "$ENV_FILE"
Restart=on-failure
RestartSec=5
KillSignal=$kill_signal
KillMode=control-group
TimeoutStopSec=120
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    chmod 644 "$unit"
done
systemctl daemon-reload
# On any failed start, leave both roles stopped; avoid an unnoticed live worker.
trap 'rc=$?; systemctl disable network-inspection-worker network-inspection-web || true; systemctl stop network-inspection-worker network-inspection-web || true; echo "Deployment failed. Inspect journalctl -u network-inspection-web and runtime/deployment/logs." >&2; exit "$rc"' ERR
systemctl start network-inspection-web
runuser -u "$SERVICE_USER" -- "$ROOT/.venv/bin/python" -m deploy.setup probe "${ARGS[@]}"
systemctl start network-inspection-worker
sleep 3
systemctl is-active --quiet network-inspection-web
systemctl is-active --quiet network-inspection-worker
systemctl enable network-inspection-web network-inspection-worker
trap - ERR
echo "Deployment complete. Configuration: $ENV_FILE"
echo "Logs: $ROOT/runtime/deployment/logs"
echo 'Allow the configured web port only for the intended network; no firewall rules were changed.'
