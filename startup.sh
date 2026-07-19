#!/usr/bin/env bash
#
# startup.sh — bootstrap and run Neural Reader (TTS backend + Postgres + frontend).
#
#   ./startup.sh init [podman|docker]   Check prerequisites, set up venv + deps,
#                                       download models, install + build frontend.
#   ./startup.sh up                     Start Postgres + the TTS backend (run.py).
#   ./startup.sh down                   SIGTERM the TTS backend, then stop Postgres.
#   ./startup.sh help                   Show usage.
#
# The container engine is resolved in this order: explicit arg to `init` →
# $CONTAINER_ENGINE → the choice saved by a previous `init` → auto-detect
# (docker preferred, then podman).

set -euo pipefail

# Always operate from the project root (this script's own directory) so the
# script works no matter where it is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"

readonly COMPOSE_FILE="docker-compose.yml"
readonly VENV_DIR=".venv"
readonly POSTGRES_SERVICE="postgres"
readonly POSTGRES_USER="natural_reader"
readonly ENGINE_STATE_FILE=".local/container-engine"  # .local/ is gitignored
readonly RUNNER_PIDFILE=".local/runner.pid"           # PID of the run.py backend

readonly MODEL_BASE_URL="https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0"
readonly MODEL_FILES=("kokoro-v1.0.onnx" "voices-v1.0.bin")

# Minimum tool versions, kept in sync with the README "Software Requirements".
# Node: Vite 7 (Rolldown) + @vitejs/plugin-react require ^20.19.0 || >=22.12.0.
# Python: Docling requires >=3.10,<4.0; doc-chat routes use 3.10+ unions.
readonly PYTHON_MIN="3.10.0"
readonly PYTHON_MAX_EXCL="4.0.0"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# vercmp A B — echo -1 if A<B, 0 if A==B, 1 if A>B (dotted numeric versions).
vercmp() {
	[[ "$1" == "$2" ]] && { echo 0; return; }
	local i
	local -a a b
	# Split each dotted operand into an array of components.
	read -ra a <<<"${1//./ }"
	read -ra b <<<"${2//./ }"
	for ((i = 0; i < ${#a[@]} || i < ${#b[@]}; i++)); do
		local x=${a[i]:-0} y=${b[i]:-0}
		x=${x%%[^0-9]*}; y=${y%%[^0-9]*}    # drop pre-release/build suffixes
		if ((10#${x:-0} > 10#${y:-0})); then echo 1; return; fi
		if ((10#${x:-0} < 10#${y:-0})); then echo -1; return; fi
	done
	echo 0
}
version_ge() { [[ "$(vercmp "$1" "$2")" != "-1" ]]; }
version_lt() { [[ "$(vercmp "$1" "$2")" == "-1" ]]; }

# Verify python3 exists and satisfies >=PYTHON_MIN, <PYTHON_MAX_EXCL.
check_python_version() {
	command -v python3 >/dev/null 2>&1 || die "python3 is not installed."
	local v
	v="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
	if version_ge "$v" "$PYTHON_MIN" && version_lt "$v" "$PYTHON_MAX_EXCL"; then
		log "Python $v OK (need >=$PYTHON_MIN, <$PYTHON_MAX_EXCL)"
	else
		die "Python $v is unsupported — need >=$PYTHON_MIN and <$PYTHON_MAX_EXCL."
	fi
}

# Verify node + npm exist and node satisfies ^20.19.0 || >=22.12.0.
check_node_version() {
	command -v node >/dev/null 2>&1 || die "node is not installed."
	command -v npm >/dev/null 2>&1 || die "npm is not installed."
	local v
	v="$(node -v)"; v="${v#v}"
	if { version_ge "$v" "20.19.0" && version_lt "$v" "21.0.0"; } \
		|| version_ge "$v" "22.12.0"; then
		log "Node.js $v OK (need ^20.19.0 or >=22.12.0)"
	else
		die "Node.js $v is unsupported — need ^20.19.0 or >=22.12.0 (Vite 7 / Rolldown)."
	fi
}

usage() {
	cat <<EOF
Usage: $0 <command> [options]

Commands:
  init [podman|docker]   Check prerequisites, create the Python venv, install
                         backend deps, download Kokoro models, install + build
                         the frontend. The engine choice is remembered.
  up                     Start the Postgres container and the TTS backend (run.py).
                         Runs in the foreground; Ctrl-C SIGTERMs the backend cleanly.
  down                   SIGTERM the TTS backend (if running) and stop Postgres.
  help                   Show this message.

Environment:
  CONTAINER_ENGINE       Override the container engine (podman|docker).
  WORKERS, HOST, PORT    Passed through to run.py (see run.py for details).
EOF
}

# Resolve which container engine to use and echo it. Order of precedence:
#   1. explicit argument  2. $CONTAINER_ENGINE  3. saved state  4. auto-detect.
resolve_engine() {
	local engine="${1:-${CONTAINER_ENGINE:-}}"

	if [[ -z "$engine" && -f "$ENGINE_STATE_FILE" ]]; then
		engine="$(<"$ENGINE_STATE_FILE")"
	fi

	if [[ -z "$engine" ]]; then
		if command -v docker >/dev/null 2>&1; then
			engine="docker"
		elif command -v podman >/dev/null 2>&1; then
			engine="podman"
		else
			die "Neither docker nor podman is installed."
		fi
	fi

	if [[ "$engine" != "podman" && "$engine" != "docker" ]]; then
		die "Invalid container engine '$engine'. Use 'podman' or 'docker'."
	fi
	command -v "$engine" >/dev/null 2>&1 || die "$engine is not installed."

	printf '%s' "$engine"
}

# Run a compose subcommand, preferring the v2 plugin ("<engine> compose") and
# falling back to the standalone binary ("<engine>-compose").
compose() {
	local engine="$1"; shift
	if "$engine" compose version >/dev/null 2>&1; then
		"$engine" compose -f "$COMPOSE_FILE" "$@"
	elif command -v "${engine}-compose" >/dev/null 2>&1; then
		"${engine}-compose" -f "$COMPOSE_FILE" "$@"
	else
		die "No compose support for $engine (need '$engine compose' or '${engine}-compose')."
	fi
}

# Download a URL using wget or curl, whichever is available. Writes to a
# temporary ".partial" file and moves it into place only on success, so an
# interrupted transfer never leaves a truncated file that later looks complete.
download() {
	local url="$1" dest="$2" tmp="${2}.partial"
	rm -f "$tmp"
	if command -v wget >/dev/null 2>&1; then
		wget -O "$tmp" "$url"
	elif command -v curl >/dev/null 2>&1; then
		curl -fL -o "$tmp" "$url"
	else
		die "Neither wget nor curl is installed; cannot download $url"
	fi
	[[ -s "$tmp" ]] || { rm -f "$tmp"; die "Download produced an empty file: $url"; }
	mv -f "$tmp" "$dest"
}

# Poll until Postgres accepts connections, with a bounded timeout. Migrations in
# run.py need the DB; if it never comes up we warn but continue, since TTS works
# without Postgres (only chat-session/RAG routes degrade to 503).
wait_for_postgres() {
	local engine="$1"
	local retries=30

	log "Waiting for Postgres to accept connections..."
	for ((i = 1; i <= retries; i++)); do
		if compose "$engine" exec -T "$POSTGRES_SERVICE" \
			pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; then
			log "Postgres is ready."
			return 0
		fi
		sleep 1
	done
	warn "Postgres not ready after ${retries}s; continuing (TTS still works, RAG may 503)."
}

# SIGTERM the run.py backend recorded in RUNNER_PIDFILE (started by `up`), if it
# is still alive, then remove the pidfile. Idempotent and safe to call when no
# runner is recorded — used both by `down` and by the `up` shutdown trap.
stop_runner() {
	[[ -f "$RUNNER_PIDFILE" ]] || return 0
	local pid; pid="$(<"$RUNNER_PIDFILE")"
	if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
		log "Stopping Neural Voice Server (SIGTERM pid $pid)"
		kill -TERM "$pid" 2>/dev/null || true
		# Give it a few seconds to shut down cleanly before giving up.
		for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
	fi
	rm -f "$RUNNER_PIDFILE"
}

cmd_init() {
	local engine
	engine="$(resolve_engine "${1:-}")"
	log "Using container engine: $engine"

	# --- Prerequisite checks (existence + correct versions) ------------------
	check_python_version
	check_node_version
	command -v wget >/dev/null 2>&1 || command -v curl >/dev/null 2>&1 \
		|| die "Neither wget nor curl is installed (needed to download models)."
	[[ -f package.json ]] || die "package.json not found — are you in the project root?"

	# --- Python backend ------------------------------------------------------
	if [[ ! -x "$VENV_DIR/bin/python" ]]; then
		log "Creating virtual environment with $(python3 -V)"
		python3 -m venv "$VENV_DIR"
	else
		log "Virtual environment already exists at $VENV_DIR/"
	fi

	log "Installing Python dependencies from requirements.txt"
	"$VENV_DIR/bin/python" -m pip install --upgrade pip
	"$VENV_DIR/bin/python" -m pip install -r requirements.txt

	# --- Kokoro model files --------------------------------------------------
	# Use -s (exists AND non-empty), not -f, so a leftover zero-byte/truncated
	# file from an aborted run is re-fetched rather than blindly trusted.
	for model in "${MODEL_FILES[@]}"; do
		if [[ -s "$model" ]]; then
			log "Model already present, skipping: $model"
		else
			[[ -e "$model" ]] && warn "Existing $model is empty/incomplete — re-downloading"
			log "Downloading $model"
			download "$MODEL_BASE_URL/$model" "$model"
		fi
	done

	# --- Frontend ------------------------------------------------------------
	log "Installing frontend dependencies (npm install)"
	npm install
	log "Building frontend (npm run build)"
	npm run build

	# Remember the engine so `up`/`down` don't need it re-specified.
	mkdir -p "$(dirname "$ENGINE_STATE_FILE")"
	printf '%s' "$engine" >"$ENGINE_STATE_FILE"

	log "Init complete. Run '$0 up' to start."
}

cmd_up() {
	local engine
	engine="$(resolve_engine)"

	[[ -x "$VENV_DIR/bin/python" ]] || die "Python environment missing. Run '$0 init' first."

	log "Starting containers ($engine)"
	compose "$engine" up -d
	wait_for_postgres "$engine"

	log "Starting Neural Voice Server (run.py)"
	mkdir -p "$(dirname "$RUNNER_PIDFILE")"
	"$VENV_DIR/bin/python" run.py &
	local runner_pid=$!
	printf '%s' "$runner_pid" >"$RUNNER_PIDFILE"
	# Trap: if this shell is interrupted (Ctrl-C), terminated, or exits for any
	# reason, SIGTERM the backend and clean up the pidfile so it never goes
	# stale. stop_runner reads the pidfile, so the handler needs no local state.
	trap stop_runner INT TERM EXIT
	wait "$runner_pid"
}

cmd_down() {
	local engine
	engine="$(resolve_engine)"
	# SIGTERM the run.py backend started by a previous `up` (via its pidfile),
	# then bring the containers down.
	stop_runner
	log "Stopping containers ($engine)"
	compose "$engine" down
}

main() {
	local command="${1:-up}"
	shift || true

	case "$command" in
		init)               cmd_init "${1:-}" ;;
		up)                 cmd_up ;;
		down)               cmd_down ;;
		help | -h | --help) usage ;;
		*)                  usage >&2; die "Unknown command: $command" ;;
	esac
}

# Run only when executed directly; `source startup.sh` loads functions for tests.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	main "$@"
fi
