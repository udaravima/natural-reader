#!/usr/bin/env bash
#
# startup.sh — bootstrap and run Neural Reader (TTS backend + Postgres + frontend).
#
#   ./startup.sh init [podman|docker]   Check prerequisites, set up venv + deps,
#                                       download models, install + build frontend.
#   ./startup.sh up                     Start the Postgres container and the TTS backend.
#   ./startup.sh down                   Stop the Postgres container.
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

readonly MODEL_BASE_URL="https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0"
readonly MODEL_FILES=("kokoro-v1.0.onnx" "voices-v1.0.bin")

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
	cat <<EOF
Usage: $0 <command> [options]

Commands:
  init [podman|docker]   Check prerequisites, create the Python venv, install
                         backend deps, download Kokoro models, install + build
                         the frontend. The engine choice is remembered.
  up                     Start the Postgres container and the TTS backend (run.py).
  down                   Stop the Postgres container.
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

# Download a URL to a destination using wget or curl, whichever is available.
download() {
	local url="$1" dest="$2"
	if command -v wget >/dev/null 2>&1; then
		wget -O "$dest" "$url"
	elif command -v curl >/dev/null 2>&1; then
		curl -fL -o "$dest" "$url"
	else
		die "Neither wget nor curl is installed; cannot download $url"
	fi
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

cmd_init() {
	local engine
	engine="$(resolve_engine "${1:-}")"
	log "Using container engine: $engine"

	# --- Prerequisite checks -------------------------------------------------
	command -v python3 >/dev/null 2>&1 || die "python3 is not installed."
	command -v npm >/dev/null 2>&1 || die "npm is not installed."
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
	for model in "${MODEL_FILES[@]}"; do
		if [[ -f "$model" ]]; then
			log "Model already present: $model"
		else
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
	exec "$VENV_DIR/bin/python" run.py
}

cmd_down() {
	local engine
	engine="$(resolve_engine)"
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

main "$@"
