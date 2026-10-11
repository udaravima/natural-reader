#!/usr/bin/env bash
#
# startup.sh — bootstrap and run Neural Reader (TTS backend + Postgres + frontend).
#
#   ./startup.sh quickstart [--single-user]
#                                       Fresh clone → running app, one command.
#   ./startup.sh init [podman|docker]   Check prerequisites, set up venv + deps,
#                                       download models, install + build frontend.
#   ./startup.sh start                  Live: the whole stack detached, the
#                                       backend from its container image.
#   ./startup.sh stop | down            Stop and remove the containers.
#   ./startup.sh rebuild | logs         Rebuild/recreate the backend; follow it.
#   ./startup.sh enable-autostart       Bring a started stack back after reboot.
#   ./startup.sh up                     Dev: the backend container runs the
#                                       working tree, auth DISABLED.
#   ./startup.sh up-with-dev-auth       Dev with the local Keycloak rig.
#                                       Walkthrough: deploy/README.md.
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
readonly KEYCLOAK_SERVICE="keycloak"
readonly KEYCLOAK_DISCOVERY_URL="http://localhost:18080/realms/natural-reader/.well-known/openid-configuration"
readonly ENGINE_STATE_FILE=".local/container-engine"  # .local/ is gitignored
readonly RUNNER_PIDFILE=".local/runner.pid"           # PID of the run.py backend
readonly ENV_FILE=".env"                              # gitignored; sourced for run.py

readonly MODEL_BASE_URL="https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0"
readonly MODEL_FILES=("kokoro-v1.0.onnx" "voices-v1.0.bin")
readonly BACKEND_SERVICE="backend"
readonly BACKEND_CONTAINER="natural-reader-backend"  # container_name in docker-compose.yml
# 1 → layer compose.dev.yml (working tree mounted) on top; set by `up`.
COMPOSE_DEV=0

# Minimum tool versions, kept in sync with the README "Software Requirements".
# Node: Vite 7 (Rolldown) + @vitejs/plugin-react require ^20.19.0 || >=22.12.0.
# Python: the chat server uses inspect.getasyncgenstate (3.12+;
# server/routers/chat_turns.py), and onnxruntime-openvino (pinned in
# requirements.txt for Kokoro) publishes wheels only for Python <3.14.
readonly PYTHON_MIN="3.12.0"
readonly PYTHON_MAX_EXCL="3.14.0"
readonly PYTHON_CANDIDATES=("python3" "python3.13" "python3.12")
PYTHON_BIN="python3"

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

# Pick a python satisfying >=PYTHON_MIN, <PYTHON_MAX_EXCL. Preference order:
# default python3 first, then python3.13, python3.12 (onnxruntime-openvino has
# no wheels for 3.14+, so a too-new python3 falls through to an older one).
# Sets PYTHON_BIN to the chosen interpreter.
check_python_version() {
	local c v
	for c in "${PYTHON_CANDIDATES[@]}"; do
		command -v "$c" >/dev/null 2>&1 || continue
		v="$("$c" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" || continue
		if version_ge "$v" "$PYTHON_MIN" && version_lt "$v" "$PYTHON_MAX_EXCL"; then
			PYTHON_BIN="$c"
			log "Python $v OK via $c (need >=$PYTHON_MIN, <$PYTHON_MAX_EXCL)"
			return
		fi
	done
	die "No python in [$PYTHON_MIN, $PYTHON_MAX_EXCL) found — install Python 3.12 or 3.13 (the chat server needs 3.12+; onnxruntime-openvino has no 3.14 wheels). With uv: uv python install 3.13"
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
  quickstart [--single-user]
                         Fresh clone → running app: writes .env and the SearXNG
                         config, installs the frontend, starts the stack
                         (live mode) and runs the frontend dev server. Needs
                         only podman/docker + compose and Node. Resumable.
                         --single-user: sign-in off, no Keycloak.
  init [podman|docker]   Check prerequisites, create the Python venv, install
                         backend deps, download Kokoro models, install + build
                         the frontend. The engine choice is remembered. (Only
                         needed for BACKEND_RUNTIME=venv and the frontend.)
  start                  Live mode: Postgres + SearXNG (+ Keycloak when it is
                         this deployment's IdP, see LOCAL_KEYCLOAK) and the
                         backend container running the code baked into its
                         image, all detached. Builds the image when needed.
  stop                   Stop and remove the containers (and a venv backend
                         started by 'up'). 'down' is the same command.
  rebuild                Rebuild the backend image and recreate only the
                         backend (after pulling code, or changing
                         DOCLING_ENABLED). Postgres and Keycloak keep running.
  logs                   Follow the backend container's output.
  enable-autostart       Podman: enable lingering + podman-restart.service so a
                         started stack comes back after a reboot. Docker: a
                         note only (its daemon already does this).
  up                     Dev: Postgres + SearXNG + the backend container
                         running your working tree, auth DISABLED (loopback
                         only). Foreground; Ctrl-C tears the stack down.
  up-with-dev-auth       Full local OIDC rig: also starts Keycloak, creates
                         .env on first run (local realm values + generated
                         SESSION_SECRET), waits for the realm import, then runs
                         the backend with auth ENABLED.
                         Log in at http://localhost:5173 as admin-user/password.
                         Walkthrough: deploy/README.md.
  help                   Show this message.

Environment:
  CONTAINER_ENGINE       Override the container engine (podman|docker).
  BACKEND_RUNTIME        container (default) or venv: 'up' / 'up-with-dev-auth'
                         run the bare-metal backend from .venv (needs 'init').
                         Set it in .env to make bare metal this checkout's
                         default; a shell value wins over .env.
  LOCAL_KEYCLOAK         auto (default) | true | false — whether 'start' runs
                         the compose Keycloak. auto: yes for the localhost
                         issuer or when KC_HOSTNAME is set.
  WORKERS, HOST, PORT    Passed to run.py from the shell or .env (see run.py).
                         Other settings reach the backend container only
                         through .env.
  AUTH_ENABLED, OIDC_*   Read from .env when it exists (see .env.example);
                         'up' overrides AUTH_ENABLED to false for the
                         single-user dev bypass (loopback bind only).
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
		# Prefer an engine that answers: a docker CLI whose daemon isn't
		# running (common next to podman) would fail every compose call.
		local candidate
		local -a pre
		for candidate in docker podman; do
			pre=()
			[[ "$candidate" == "podman" ]] && pre=(env -u XDG_DATA_HOME)
			if command -v "$candidate" >/dev/null 2>&1 \
				&& "${pre[@]}" "$candidate" info >/dev/null 2>&1; then
				engine="$candidate"
				break
			fi
		done
	fi
	if [[ -z "$engine" ]]; then
		# Neither answers: keep the old order, and let the first call say why.
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

# Run a compose subcommand. For podman, prefer the dedicated `podman-compose`
# binary: podman's built-in `podman compose` is only a shim that delegates to the
# first external provider it finds, which is frequently a legacy/broken
# docker-compose (fails with "Not supported URL scheme http+docker") even when the
# podman socket is fine. For docker, prefer the v2 plugin ("docker compose"). Both
# fall back to the standalone "<engine>-compose" binary.
#
# Every podman invocation is wrapped in `env -u XDG_DATA_HOME`: under snap-podman
# an exported XDG_DATA_HOME breaks podman's socket resolution and every compose
# call fails. The unset is scoped to the invocation only.
# Compose files for this invocation: the base file, the podman overlay
# (keep-id), the assistant-profile overlay when export_backend_env found a
# readable profile file, and the dev overlay when COMPOSE_DEV=1.
compose_files() {
	local engine="$1"
	printf '%s\n' "$COMPOSE_FILE"
	[[ "$engine" == "podman" ]] && printf '%s\n' "compose.podman.yml"
	[[ -n "${NR_PROFILE_FILE:-}" ]] && printf '%s\n' "compose.profile.yml"
	[[ "${COMPOSE_DEV:-0}" == "1" ]] && printf '%s\n' "compose.dev.yml"
	return 0
}

compose() {
	local engine="$1"; shift
	local -a prefix=()
	local -a files=()
	local f
	while IFS= read -r f; do files+=(-f "$f"); done < <(compose_files "$engine")
	if [[ "$engine" == "podman" ]]; then
		prefix=(env -u XDG_DATA_HOME)
	fi
	if [[ "$engine" == "podman" ]] && command -v podman-compose >/dev/null 2>&1; then
		"${prefix[@]}" podman-compose "${files[@]}" "$@"
	elif "${prefix[@]}" "$engine" compose version >/dev/null 2>&1; then
		"${prefix[@]}" "$engine" compose "${files[@]}" "$@"
	elif command -v "${engine}-compose" >/dev/null 2>&1; then
		# docker-compose v1 can't read env_file `required:` in docker-compose.yml.
		if [[ "$("${prefix[@]}" "${engine}-compose" version 2>/dev/null)" == *"version 1."* ]]; then
			die "${engine}-compose v1 can't read docker-compose.yml — install Docker Compose v2 (2.24 or newer)."
		fi
		"${prefix[@]}" "${engine}-compose" "${files[@]}" "$@"
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

# Keycloak stores its realm in the app database's `keycloak` schema, which
# deploy/postgres/init/00-create-keycloak-schema.sql only creates on a FRESH
# data volume. On a reused volume that script never runs, Keycloak fails its
# first migration against the missing schema, and the realm import dies
# silently. Creating it here — BEFORE Keycloak starts — covers both cases
# (CREATE SCHEMA IF NOT EXISTS is a no-op on fresh volumes). Best-effort,
# warn-and-continue, mirroring wait_for_postgres.
ensure_keycloak_schema() {
	local engine="$1"
	log "Ensuring 'keycloak' schema exists in Postgres..."
	if compose "$engine" exec -T "$POSTGRES_SERVICE" \
		psql -U "$POSTGRES_USER" -d "$POSTGRES_USER" \
		-c 'CREATE SCHEMA IF NOT EXISTS keycloak' >/dev/null 2>&1; then
		log "Keycloak schema present."
	else
		warn "Could not ensure the keycloak schema (Postgres not ready?). If the realm import fails, see deploy/postgres/init/00-create-keycloak-schema.sql."
	fi
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

# Bootstrap searxng/settings.yml from the committed template on first run, so
# `up` brings SearXNG up with JSON output enabled (it's OFF by default → the
# web_search tool would 403 and silently fall back to snippets). Mirrors copying
# .env.example → .env: the real file is gitignored because it holds a secret_key.
# Idempotent — a no-op once the file exists (including when a previous `up` left
# it owned by the container's user, which we must not clobber).
ensure_searxng_config() {
	local example="searxng/settings.yml.example"
	local target="searxng/settings.yml"
	[[ -f "$target" ]] && return 0
	[[ -f "$example" ]] || { warn "$example missing — SearXNG may 403 (no JSON) until you create $target."; return 0; }
	log "Creating $target from template (first run)"
	cp "$example" "$target"
	sed -i "s/CHANGE_ME_openssl_rand_hex_32/$(generate_secret)/" "$target"
}

# A 64-character hex secret (above the backend's 32-char SESSION_SECRET
# minimum). No Python needed: quickstart must work on a host without it.
generate_secret() {
	if command -v openssl >/dev/null 2>&1; then
		openssl rand -hex 32
	elif [[ -r /dev/urandom ]]; then
		head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
		printf '\n'
	else
		die "Need openssl or /dev/urandom to generate a secret; add one to $ENV_FILE manually."
	fi
}

# Create .env on first run. $1 = dev (local Keycloak rig; mirrors
# deploy/README.md) or single-user (sign-in off, loopback only). Idempotent —
# an existing .env is never touched, so manual edits survive.
ensure_env_file() {
	local profile="${1:-dev}"
	[[ -f "$ENV_FILE" ]] && return 0
	case "$profile" in
		dev)
			log "Creating $ENV_FILE with the local-dev OIDC rig values (first run)"
			{
				echo "# Created by ./startup.sh — local-dev OIDC rig."
				echo "# Full annotated reference: .env.example. This file is gitignored."
				echo "# bash-sourced: one KEY=value per line, no inline comments."
				echo "OIDC_ISSUER=http://localhost:18080/realms/natural-reader"
				echo "OIDC_CLIENT_ID=natural-reader"
				echo "OIDC_CLIENT_SECRET=natural-reader-dev-secret"
				echo "OIDC_REDIRECT_URL=http://localhost:5173/v1/auth/callback"
				printf 'SESSION_SECRET=%s\n' "$(generate_secret)"
				echo "COOKIE_SECURE=false"
				echo "BOOTSTRAP_ADMIN_EMAIL=admin@example.com"
			} >"$ENV_FILE"
			;;
		single-user)
			log "Creating $ENV_FILE for single-user use (sign-in off, this machine only)"
			{
				echo "# Created by ./startup.sh quickstart --single-user."
				echo "# Sign-in is off; the backend refuses this on a non-loopback bind."
				echo "# Full annotated reference: .env.example. This file is gitignored."
				echo "AUTH_ENABLED=false"
			} >"$ENV_FILE"
			;;
		*) die "Unknown .env profile '$profile' (dev or single-user)." ;;
	esac
}

# Export every KEY=value in .env into this shell (and thus into run.py's
# environment — the backend has no dotenv loader of its own). docker-compose
# also reads .env natively for its \${VAR:-default} interpolations.
load_env_file() {
	[[ -f "$ENV_FILE" ]] || return 0
	log "Loading $ENV_FILE"
	set -a
	# shellcheck disable=SC1091
	source "$ENV_FILE"
	set +a
}

# One KEY's value from .env without sourcing it, for decisions made before
# load_env_file runs. The last assignment wins, as when bash sources it.
env_file_value() {
	[[ -f "$ENV_FILE" ]] || return 0
	sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d "\"'"
}

# Is the compose Keycloak this deployment's IdP? LOCAL_KEYCLOAK=auto|true|false
# (default auto). auto: yes when the issuer is the local rig, or when
# KC_HOSTNAME is set — it only configures the compose Keycloak, so a live site
# fronting it with a public hostname (issuer https://auth.example.com) counts.
local_keycloak_wanted() {
	case "${LOCAL_KEYCLOAK:-auto}" in
		true) return 0 ;;
		false) return 1 ;;
		auto)
			case "${OIDC_ISSUER:-}" in
				http://localhost:18080/* | http://127.0.0.1:18080/*) return 0 ;;
			esac
			[[ -n "${KC_HOSTNAME:-}" ]] && return 0
			return 1
			;;
		*) die "LOCAL_KEYCLOAK must be auto, true or false (got '${LOCAL_KEYCLOAK}')." ;;
	esac
}

# Build the image with Docling exactly when .env enables it (same parsing as
# server/services/docling_convert.py: 1/true/yes, any case).
docling_build_flag() {
	local v="${DOCLING_ENABLED:-false}"
	case "${v,,}" in
		1 | true | yes) printf 'true\n' ;;
		*) printf 'false\n' ;;
	esac
}

# .env path settings name HOST paths; the container pins its own. They reach
# compose as NR_* (podman-compose fills a volume's ${VAR} from the service's
# own environment block, and can't nest ${A:-${B}} defaults). DOC_STORAGE_DIR
# wins over the legacy PDF_STORAGE_DIR, as in the app. The folders are made
# here so a rootful engine doesn't create them as root.
export_host_paths() {
	NR_DATA_DIR="${DOC_STORAGE_DIR:-${PDF_STORAGE_DIR:-./data/pdfs}}"
	NR_LOG_DIR="${LOG_DIR:-./logs}"
	mkdir -p "$NR_DATA_DIR" "$NR_LOG_DIR"
	# Absolute: compose reads a bare "data/pdfs" as a named volume.
	NR_DATA_DIR="$(cd "$NR_DATA_DIR" && pwd)"
	NR_LOG_DIR="$(cd "$NR_LOG_DIR" && pwd)"
	export NR_DATA_DIR NR_LOG_DIR
}

# Everything compose needs to build and run the backend for this user.
export_backend_env() {
	export_host_paths
	NR_WITH_DOCLING="$(docling_build_flag)"
	NR_APP_UID="$(id -u)"
	[[ "$NR_APP_UID" == "0" ]] && NR_APP_UID=1000
	export NR_WITH_DOCLING NR_APP_UID
	unset NR_PROFILE_FILE
	if [[ -n "${CHAT_ASSISTANT_PROFILE_FILE:-}" ]]; then
		if [[ -r "$CHAT_ASSISTANT_PROFILE_FILE" && -f "$CHAT_ASSISTANT_PROFILE_FILE" ]]; then
			NR_PROFILE_FILE="$(cd "$(dirname "$CHAT_ASSISTANT_PROFILE_FILE")" && pwd)/$(basename "$CHAT_ASSISTANT_PROFILE_FILE")"
			export NR_PROFILE_FILE
		else
			warn "CHAT_ASSISTANT_PROFILE_FILE is not readable; the assistant gets no file profile."
		fi
	fi
	if [[ -n "${LOG_AUDIT_FILE:-}" ]]; then
		warn "LOG_AUDIT_FILE is ignored by the container: audit.log is written under $NR_LOG_DIR."
	fi
}

# True if something is already listening on host:port (bash /dev/tcp probe —
# no dependency on ss/lsof/nc).
port_in_use() {
	local host="$1" port="$2"
	(exec 3<>"/dev/tcp/$host/$port") >/dev/null 2>&1
}

# Refuse to start a second backend on an occupied port. Without this, run.py
# dies instantly with "address already in use", `wait` returns, the EXIT trap
# tears the freshly-started containers down, and `up` exits looking like it
# worked while the OLD backend (possibly with stale env) keeps serving.
check_backend_port_free() {
	local host="${HOST:-127.0.0.1}" port="${PORT:-8000}"
	if [[ -f "$RUNNER_PIDFILE" ]] && kill -0 "$(cat "$RUNNER_PIDFILE")" 2>/dev/null; then
		die "Backend already running (pid $(<"$RUNNER_PIDFILE")) — run '$0 down' first."
	fi
	# A bind on all interfaces is reachable via loopback; probe that.
	[[ "$host" == "0.0.0.0" || "$host" == "::" ]] && host="127.0.0.1"
	if port_in_use "$host" "$port"; then
		die "Port $port is already in use (HOST=$host) — another backend is likely still running. Stop it ('$0 down', or kill the process) and retry."
	fi
}

# Poll until Keycloak serves the realm's OIDC discovery document. The realm
# import takes ~20–40s on first container start, and an early login attempt
# against a half-up Keycloak fails confusingly. Warn-and-continue on timeout:
# TTS still serves; /v1/auth/login just 503s until Keycloak is actually up.
wait_for_keycloak() {
	local retries=120 i=1 ok=0
	log "Waiting for Keycloak realm import..."
	for ((i = 1; i <= retries; i++)); do
		if command -v curl >/dev/null 2>&1; then
			curl -sf -o /dev/null "$KEYCLOAK_DISCOVERY_URL" && { ok=1; break; }
		elif command -v wget >/dev/null 2>&1; then
			wget -q -O /dev/null "$KEYCLOAK_DISCOVERY_URL" && { ok=1; break; }
		else
			warn "Neither curl nor wget found — skipping the Keycloak readiness check."
			return 0
		fi
		sleep 2
	done
	if ((ok)); then
		log "Keycloak realm is ready."
	else
		warn "Keycloak discovery not answering after $((retries * 2))s; login will 503 until it is up."
	fi
}

# True when a GET to $1 succeeds. Without curl or wget there is no way to
# check, so report success and let the caller's next step surface problems.
http_ok() {
	if command -v curl >/dev/null 2>&1; then
		curl -sf -o /dev/null "$1"
	elif command -v wget >/dev/null 2>&1; then
		wget -q -O /dev/null "$1"
	else
		return 0
	fi
}

# Poll the backend's health route. The first start loads the 300 MB voice
# model, which takes a while on CPU.
wait_for_backend() {
	local url="http://127.0.0.1:${PORT:-8000}/v1/health" i
	log "Waiting for the backend at $url ..."
	for ((i = 1; i <= 90; i++)); do
		if http_ok "$url"; then
			log "Backend is ready."
			return 0
		fi
		sleep 2
	done
	warn "Backend not answering after 180s — see '$0 logs'."
	return 1
}

# Prints "live" when this project's backend container runs with restart:
# always (start / rebuild), "dev" when an `up` runs it (restart: no), and
# nothing when none runs. A live backend holding the port is ours, so `start`
# may bring the stack up again; a dev one must not be replaced, or that
# terminal's Ctrl-C teardown later takes the new live stack down with it.
backend_container_mode() {
	local engine="$1" out
	local -a prefix=()
	[[ "$engine" == "podman" ]] && prefix=(env -u XDG_DATA_HOME)
	out="$("${prefix[@]}" "$engine" container inspect -f '{{.State.Running}} {{.HostConfig.RestartPolicy.Name}}' "$BACKEND_CONTAINER" 2>/dev/null || true)"
	case "$out" in
		"true always") printf 'live\n' ;;
		true*) printf 'dev\n' ;;
	esac
	return 0
}

# Postgres + SearXNG, then (when this deployment's IdP is the compose one)
# the keycloak schema and Keycloak. Shared by `start` and the container `up`.
start_services() {
	local engine="$1" with_keycloak="$2"
	ensure_searxng_config
	log "Starting containers ($engine): $POSTGRES_SERVICE searxng"
	compose "$engine" up -d "$POSTGRES_SERVICE" searxng
	wait_for_postgres "$engine"
	if [[ "$with_keycloak" == "1" ]]; then
		ensure_keycloak_schema "$engine"
		log "Starting containers ($engine): $KEYCLOAK_SERVICE"
		compose "$engine" up -d "$KEYCLOAK_SERVICE"
		wait_for_keycloak
	fi
}

# Live mode: everything detached, the backend running the code baked into
# its image. Restarts with the machine after `enable-autostart`.
cmd_start() {
	local engine
	engine="$(resolve_engine)"
	load_env_file
	export_backend_env
	case "$(backend_container_mode "$engine")" in
		live) ;;
		dev) die "A dev backend ('$0 up') is running — Ctrl-C it in its terminal first." ;;
		*) check_backend_port_free ;;
	esac
	local kc=0
	local_keycloak_wanted && kc=1
	start_services "$engine" "$kc"
	log "Building the backend image (Docling: $NR_WITH_DOCLING)"
	compose "$engine" build "$BACKEND_SERVICE"
	compose "$engine" up -d "$BACKEND_SERVICE"
	wait_for_backend
	log "Running (live mode). Logs: '$0 logs' · stop: '$0 stop' · after code changes: '$0 rebuild' · survive reboots: '$0 enable-autostart'"
}

# Rebuild the image and recreate only the backend; Postgres, SearXNG and
# Keycloak keep running untouched.
cmd_rebuild() {
	local engine
	engine="$(resolve_engine)"
	load_env_file
	export_backend_env
	[[ "$(backend_container_mode "$engine")" == "dev" ]] \
		&& die "A dev backend ('$0 up') is running — Ctrl-C it in its terminal first; it already runs your working tree."
	log "Rebuilding the backend image (Docling: $NR_WITH_DOCLING)"
	compose "$engine" build "$BACKEND_SERVICE"
	compose "$engine" up -d --force-recreate --no-deps "$BACKEND_SERVICE"
	wait_for_backend
}

cmd_logs() {
	local engine
	engine="$(resolve_engine)"
	compose "$engine" logs -f "$BACKEND_SERVICE"
}

# Podman: user containers come back after a reboot only with lingering on
# (user services run without a login session) and podman-restart.service
# enabled (it starts restart-policy=always containers). Docker's daemon does
# this itself.
cmd_enable_autostart() {
	local engine
	engine="$(resolve_engine)"
	if [[ "$engine" == "docker" ]]; then
		log "Docker restarts 'restart: always' containers when its daemon starts — nothing to change here. Make sure the daemon starts at boot (e.g. systemctl enable docker)."
		return 0
	fi
	log "Running: loginctl enable-linger $USER  (your user services keep running without a login session)"
	loginctl enable-linger "$USER"
	log "Running: systemctl --user enable --now podman-restart.service  (restarts restart-policy=always containers at boot)"
	systemctl --user enable --now podman-restart.service
	log "Done. A stack started with '$0 start' now comes back after a reboot."
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
	venv_py="$("$VENV_DIR/bin/python" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
	if [[ ! -x "$VENV_DIR/bin/python" ]]; then
		log "Creating virtual environment with $($PYTHON_BIN -V)"
		"$PYTHON_BIN" -m venv "$VENV_DIR"
	elif [[ "$venv_py" != "$($PYTHON_BIN -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" ]]; then
		log "Recreating virtual environment: was $venv_py, need $($PYTHON_BIN -V)"
		rm -rf "$VENV_DIR"
		"$PYTHON_BIN" -m venv "$VENV_DIR"
	else
		log "Virtual environment already exists at $VENV_DIR/ ($venv_py)"
	fi

	log "Installing Python dependencies from requirements.txt"
	"$VENV_DIR/bin/python" -m pip install --upgrade pip
	"$VENV_DIR/bin/python" -m pip install -r requirements.txt -r requirements-docling.txt

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

# Everything quickstart needs from the host — no Python. Prints the engine.
check_quickstart_prereqs() {
	local engine
	engine="$(resolve_engine)"
	# Subshell: compose() exits when no compose exists, which would end this
	# function's own $(…) silently instead of reaching the message below.
	( compose "$engine" config ) >/dev/null 2>&1 \
		|| die "Compose for $engine is missing or can't read docker-compose.yml — install podman-compose 1.1+ (podman) or the Docker Compose plugin 2.24+ (docker)."
	check_node_version >&2
	command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
		|| die "Neither curl nor wget is installed (needed to check the services came up)."
	printf '%s' "$engine"
}

quickstart_summary() {
	local profile="$1"
	log "Backend is running at http://127.0.0.1:${PORT:-8000} (live mode; it keeps running after this)."
	if [[ "$profile" == "single-user" ]]; then
		log "Sign-in is off (single-user, this machine only)."
	else
		log "Sign in at http://localhost:5173 as admin-user / password"
	fi
	log "Next: '$0 stop' stops everything · '$0 logs' follows the backend · '$0 enable-autostart' brings it back after reboots"
	log "Production? Start from .env.example and docs/DEPLOYMENT.md — quickstart only writes a local dev .env."
}

# Fresh clone → running app. Each step is skipped when already done, so a
# re-run resumes after a failure. Ends by running the frontend dev server in
# the foreground; Ctrl-C stops only the frontend.
cmd_quickstart() {
	local profile="dev"
	case "${1:-}" in
		"") ;;
		--single-user) profile="single-user" ;;
		*) die "Unknown option '$1' (quickstart takes only --single-user)." ;;
	esac
	local engine
	engine="$(check_quickstart_prereqs)"
	mkdir -p "$(dirname "$ENGINE_STATE_FILE")"
	printf '%s' "$engine" >"$ENGINE_STATE_FILE"
	ensure_env_file "$profile"
	ensure_searxng_config
	if [[ -f node_modules/.package-lock.json && node_modules/.package-lock.json -nt package-lock.json ]]; then
		log "Frontend dependencies are up to date."
	else
		log "Installing frontend dependencies (npm install)"
		npm install
	fi
	CONTAINER_ENGINE="$engine" cmd_start
	quickstart_summary "$profile"
	log "Starting the frontend dev server — Ctrl-C stops only the frontend; '$0 stop' stops the rest."
	exec npm run dev
}

# Dev: the backend container runs the working tree (compose.dev.yml), in the
# foreground via its logs; Ctrl-C tears the stack down as before.
# BACKEND_RUNTIME=venv (shell, or .env to make it this checkout's default)
# runs the bare-metal backend instead.
cmd_up() {
	local mode="$1"
	local runtime="${BACKEND_RUNTIME:-$(env_file_value BACKEND_RUNTIME)}"
	case "${runtime:-container}" in
		venv) cmd_up_venv "$mode"; return ;;
		container) ;;
		*) die "BACKEND_RUNTIME must be container or venv (got '${runtime}')." ;;
	esac
	local engine
	engine="$(resolve_engine)"
	if [[ "$mode" == "dev-auth" ]]; then
		ensure_env_file dev
		load_env_file
		[[ -n "${OIDC_ISSUER:-}" ]] || warn "OIDC_ISSUER is not set — login will return 503. Check $ENV_FILE."
		log "Auth ENABLED (OIDC issuer: ${OIDC_ISSUER:-<unset>})"
	else
		load_env_file
		AUTH_ENABLED=false
		export AUTH_ENABLED
		log "Auth DISABLED (single-user dev bypass; loopback bind only)"
	fi
	export_backend_env
	check_backend_port_free
	# Set before anything starts (but after the port check, so a refused run
	# never tears down a stack it didn't start): Ctrl-C during a long first
	# build still stops the services, which would otherwise stay up.
	trap "trap - INT TERM EXIT; log 'Stopping containers ($engine)'; COMPOSE_DEV=1 compose $engine down" INT TERM EXIT
	local kc=0
	[[ "$mode" == "dev-auth" ]] && kc=1
	start_services "$engine" "$kc"
	COMPOSE_DEV=1
	log "Starting the backend container with your working tree mounted"
	compose "$engine" build "$BACKEND_SERVICE"
	compose "$engine" up -d "$BACKEND_SERVICE"
	wait_for_backend || true
	if [[ "$mode" == "dev-auth" ]]; then
		log "Ready — sign in at http://localhost:5173 (Keycloak user: admin-user / password)"
	else
		log "Ready — frontend: npm run dev (then http://localhost:5173)"
	fi
	log "Code edits apply on restart: Ctrl-C and run this again."
	compose "$engine" logs -f "$BACKEND_SERVICE"
}

# The bare-metal backend (BACKEND_RUNTIME=venv): run.py from .venv. $1 =
# "dev-auth" (Keycloak rig, auth on) or "" (quick single-user dev, auth
# bypassed). Foreground: Ctrl-C SIGTERMs the backend and brings the
# containers down via the EXIT trap.
cmd_up_venv() {
	local mode="$1"
	local engine
	engine="$(resolve_engine)"

	[[ -x "$VENV_DIR/bin/python" ]] || die "Python environment missing. Run '$0 init' first."

	if [[ "$mode" == "dev-auth" ]]; then
		ensure_env_file
		load_env_file
		if [[ -z "${OIDC_ISSUER:-}" ]]; then
			warn "OIDC_ISSUER is not set — login will return 503. Check $ENV_FILE."
		fi
		log "Auth ENABLED (OIDC issuer: ${OIDC_ISSUER:-<unset>})"
	else
		load_env_file
		# Quick single-user dev: explicit bypass of the OIDC flow. The backend's
		# startup guard refuses this on a non-loopback bind (server/app.py), so
		# it can never leak onto an exposed server.
		AUTH_ENABLED=false
		export AUTH_ENABLED
		log "Auth DISABLED (single-user dev bypass; loopback bind only)"
	fi

	check_backend_port_free
	ensure_searxng_config

	# Keycloak must NOT start in this first batch: it needs the `keycloak`
	# schema, which is created right after Postgres is ready (see
	# ensure_keycloak_schema). Starting it together with Postgres races its
	# first migration against the schema creation on reused volumes.
	local -a services=("$POSTGRES_SERVICE" "searxng")

	log "Starting containers ($engine): ${services[*]}"
	compose "$engine" up -d "${services[@]}"
	wait_for_postgres "$engine"
	if [[ "$mode" == "dev-auth" ]]; then
		ensure_keycloak_schema "$engine"
		log "Starting containers ($engine): $KEYCLOAK_SERVICE"
		compose "$engine" up -d "$KEYCLOAK_SERVICE"
		wait_for_keycloak
	fi

	log "Starting Neural Voice Server (run.py)"
	mkdir -p "$(dirname "$RUNNER_PIDFILE")"
	"$VENV_DIR/bin/python" run.py &
	local runner_pid=$!
	printf '%s' "$runner_pid" >"$RUNNER_PIDFILE"
	# Trap: if this shell is interrupted (Ctrl-C), terminated, or exits for any
	# reason, SIGTERM the backend AND bring the containers down (engine-aware) so
	# nothing is left running. Clear the trap first so an INT followed by the EXIT
	# trap can't run the teardown twice.
	#
	# NB: the trap body is DOUBLE-quoted so `$engine` is expanded (baked in) now,
	# while it's in scope. If run.py exits on its own (e.g. the port is already in
	# use), `wait` returns and the EXIT trap fires in global scope where the local
	# `engine` no longer exists — a deferred `$engine` would then abort teardown
	# under `set -u` ("engine: unbound variable").
	trap "trap - INT TERM EXIT; stop_runner; log 'Stopping containers ($engine)'; compose $engine down" INT TERM EXIT
	if [[ "$mode" == "dev-auth" ]]; then
		log "Ready — sign in at http://localhost:5173 (Keycloak user: admin-user / password)"
		log "Add a second user at http://localhost:18080/admin (admin/admin) to test the approval flow — see deploy/README.md"
	else
		log "Ready — frontend: npm run dev (then http://localhost:5173)"
	fi
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
		quickstart)         cmd_quickstart "${1:-}" ;;
		init)               cmd_init "${1:-}" ;;
		up)                 cmd_up "" ;;
		up-with-dev-auth)   cmd_up "dev-auth" ;;
		start)              cmd_start ;;
		stop | down)        cmd_down ;;
		rebuild)            cmd_rebuild ;;
		logs)               cmd_logs ;;
		enable-autostart)   cmd_enable_autostart ;;
		help | -h | --help) usage ;;
		*)                  usage >&2; die "Unknown command: $command" ;;
	esac
}

# Run only when executed directly; `source startup.sh` loads functions for tests.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	main "$@"
fi
