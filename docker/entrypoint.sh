#!/bin/bash
# Runs the trading engine and the dashboard side by side in one container,
# sharing the same /data volume. Each is wrapped in its own restart loop so a
# crash in one (e.g. a transient DexScreener API error) doesn't take the
# other down or require the whole machine to restart.
set -u

DASHBOARD_PORT="${DASHBOARD_PORT:-8787}"

run_forever() {
    local name="$1"
    shift
    while true; do
        "$@"
        local code=$?
        echo "[entrypoint] $name exited (code $code); restarting in 5s" >&2
        sleep 5
    done
}

term_handler() {
    echo "[entrypoint] received stop signal, shutting down" >&2
    kill -TERM 0 2>/dev/null
    exit 0
}
trap term_handler TERM INT

run_forever engine python -m memecoin_trader.cli run &
run_forever dashboard python -m memecoin_trader.cli dashboard --host 0.0.0.0 --port "$DASHBOARD_PORT" &

wait -n
