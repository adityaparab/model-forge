#!/usr/bin/env bash
# Fresh clone to first gate: clone the repository into a temporary directory, run `make demo`
# there on its own Compose project, and wait for the demo run to reach `awaiting_scope`.
#
# Usage: scripts/fresh_clone_test.sh [REMOTE_URL] [--dry-run | --port-check-only]
#   REMOTE_URL         defaults to this checkout's `origin` remote
#   --dry-run          print each command it would run, one per line, and run none
#   --port-check-only  only check that nothing answers on port 8000
#
# Environment:
#   MODELFORGE_FRESH_PORT       host port for the clone's Postgres (default 5434)
#   MODELFORGE_FRESH_TIMEOUT_S  seconds to wait for awaiting_scope (default 1200)
#
# Exit codes: 0 reached awaiting_scope; 2 usage error; 3 port 8000 busy;
# 4 timed out (logs in /tmp/fresh-clone-logs); 5 `make demo` failed or printed no run id.
set -euo pipefail

API_PORT=8000
API_URL="http://localhost:${API_PORT}/api/v1"
PROJECT_NAME=modelforge-fresh
FRESH_PORT="${MODELFORGE_FRESH_PORT:-5434}"
TIMEOUT_S="${MODELFORGE_FRESH_TIMEOUT_S:-1200}"
LOG_DIR=/tmp/fresh-clone-logs
POLL_S=5

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

dry_run=0
port_check_only=0
remote=""
for arg in "$@"; do
	case "$arg" in
	--dry-run) dry_run=1 ;;
	--port-check-only) port_check_only=1 ;;
	-h | --help)
		usage
		exit 0
		;;
	-*)
		echo "fresh-clone: unknown option $arg" >&2
		exit 2
		;;
	*) remote="$arg" ;;
	esac
done

# Something answers when curl connects at all: an HTTP reply (0 or 22), a timeout from a silent
# listener (28) or an empty or broken reply. Only "could not connect" (7) means the port is free.
port_is_free() {
	local rc=0
	curl -sf -o /dev/null --max-time 2 "http://localhost:${API_PORT}/api/v1/health" || rc=$?
	[ "$rc" -eq 7 ]
}

check_port() {
	if ! port_is_free; then
		echo "fresh-clone: something already answers on port ${API_PORT}" >&2
		exit 3
	fi
}

if [ "$port_check_only" -eq 1 ]; then
	check_port
	echo "fresh-clone: port ${API_PORT} is free"
	exit 0
fi

if [ -z "$remote" ]; then
	remote="$(git -C "$(dirname "$0")/.." remote get-url origin 2>/dev/null || true)"
	if [ -z "$remote" ]; then
		echo "fresh-clone: no REMOTE_URL given and no origin remote to default to" >&2
		exit 2
	fi
fi

if [ "$dry_run" -eq 1 ]; then
	cat <<STEPS
curl -sf -o /dev/null --max-time 2 http://localhost:${API_PORT}/api/v1/health  # must not connect
mktemp -d
git clone --depth 1 ${remote} <tmp>/model-forge
cd <tmp>/model-forge
export COMPOSE_PROJECT_NAME=${PROJECT_NAME}
export MODELFORGE_POSTGRES_PORT=${FRESH_PORT}
MODELFORGE_DEMO_NO_OPEN=1 make demo
curl -sf -H 'X-ModelForge-User: alice' ${API_URL}/runs/<id>  # every ${POLL_S} s until awaiting_scope, at most ${TIMEOUT_S} s
docker compose down -v
rm -rf <tmp>
STEPS
	exit 0
fi

check_port

start=$SECONDS
work="$(mktemp -d)"
clone="$work/model-forge"

cleanup() {
	local rc=$?
	cd /
	if [ -d "$clone" ]; then
		(cd "$clone" && COMPOSE_PROJECT_NAME="$PROJECT_NAME" MODELFORGE_POSTGRES_PORT="$FRESH_PORT" \
			docker compose down -v) || true
	fi
	# The containers write artifacts into the clone as root, which the calling user cannot
	# remove on Linux; fall back to passwordless sudo (GitHub runners) and never let the
	# cleanup change the exit code of the measurement.
	rm -rf "$work" 2>/dev/null || sudo -n rm -rf "$work" 2>/dev/null \
		|| echo "fresh-clone: could not remove $work (container-owned files); remove it by hand" >&2
	exit "$rc"
}
trap cleanup EXIT

dump_logs() {
	mkdir -p "$LOG_DIR"
	local log
	log="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ).log"
	(cd "$clone" && docker compose logs --no-color --tail 200 api worker) >"$log" 2>&1 || true
	echo "fresh-clone: last 50 lines of api and worker logs (full tail in $log):" >&2
	tail -n 50 "$log" >&2
}

git clone --depth 1 "$remote" "$clone"
cd "$clone"
export COMPOSE_PROJECT_NAME="$PROJECT_NAME"
export MODELFORGE_POSTGRES_PORT="$FRESH_PORT"

if ! MODELFORGE_DEMO_NO_OPEN=1 make demo | tee "$work/demo.out"; then
	echo "fresh-clone: make demo failed" >&2
	dump_logs
	exit 5
fi
run_id="$(sed -n 's/^run //p' "$work/demo.out" | tail -n 1)"
if [ -z "$run_id" ]; then
	echo "fresh-clone: make demo printed no run id" >&2
	dump_logs
	exit 5
fi

while :; do
	body="$(curl -sf -H 'X-ModelForge-User: alice' "$API_URL/runs/$run_id" || true)"
	if printf '%s' "$body" | grep -Eq '"status": *"awaiting_scope"'; then
		echo "fresh-clone: awaiting_scope after $((SECONDS - start)) s"
		exit 0
	fi
	if [ $((SECONDS - start)) -ge "$TIMEOUT_S" ]; then
		echo "fresh-clone: run $run_id did not reach awaiting_scope within ${TIMEOUT_S} s" >&2
		dump_logs
		exit 4
	fi
	sleep "$POLL_S"
done
