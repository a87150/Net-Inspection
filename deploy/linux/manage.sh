#!/usr/bin/env bash
# Operate the deployed Web and Worker systemd units (status/start/stop/restart/probe/logs).
#
#   sudo bash deploy/linux/manage.sh status
#   sudo bash deploy/linux/manage.sh restart
#   sudo bash deploy/linux/manage.sh logs web 200
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

action="${1:-status}"
target="${2:-both}"
tail_n="${3:-80}"

case "$target" in
    both) roles=(web worker) ;;
    web|worker) roles=("$target") ;;
    *) echo "Unknown role: $target" >&2; exit 2 ;;
esac

unit() { printf 'network-inspection-%s.service\n' "$1"; }

status_one() {
    local u active enabled
    u="$(unit "$1")"
    if ! systemctl cat "$u" >/dev/null 2>&1; then
        printf '%-8s %-34s %s\n' "$1" "$u" 'missing'
        return 1
    fi
    active="$(systemctl is-active "$u" 2>/dev/null || true)"
    enabled="$(systemctl is-enabled "$u" 2>/dev/null || true)"
    printf '%-8s %-34s %-10s %s\n' "$1" "$u" "$active" "$enabled"
}

case "$action" in
    status)
        found=0
        for role in "${roles[@]}"; do
            status_one "$role" && found=1
        done
        if [[ "$found" == 0 ]]; then
            echo 'No deployment found. Run deploy/linux/install.sh first.' >&2
            exit 1
        fi
        ;;
    start|restart)
        for role in web worker; do systemctl restart "$(unit "$role")"; done
        sleep 3
        for role in "${roles[@]}"; do status_one "$role" || true; done
        ;;
    stop)
        for role in worker web; do systemctl stop "$(unit "$role")"; done
        ;;
    enable)
        for role in web worker; do systemctl enable "$(unit "$role")"; done
        ;;
    disable)
        for role in worker web; do systemctl disable "$(unit "$role")"; done
        ;;
    probe)
        "$PYTHON" -m deploy.setup probe --env-file "$ENV_FILE"
        ;;
    logs)
        log="$ROOT/runtime/deployment/logs/$target.log"
        [[ -f "$log" ]] || { echo "No log yet: $log" >&2; exit 1; }
        tail -n "$tail_n" "$log"
        ;;
    *)
        echo "Usage: manage.sh {status|start|stop|restart|enable|disable|probe|logs} [web|worker|both] [lines]" >&2
        exit 2
        ;;
esac
