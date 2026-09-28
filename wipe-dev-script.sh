#!/usr/bin/env bash
#
# wipe-dev-script.sh — empty the document library of the LOCAL dev database.
#
#   ./wipe-dev-script.sh                 Ask, then wipe documents, libraries,
#                                        projects and the stored files.
#   ./wipe-dev-script.sh --yes           Don't ask.
#   ./wipe-dev-script.sh --keep-projects Keep projects and their members (only
#                                        their documents go).
#   ./wipe-dev-script.sh --clear-pins    Also empty chat pins (they hold copies
#                                        of document text).
#   ./wipe-dev-script.sh --help          Show this.
#
# Never touches users, login sessions, access tokens, chats, usage or the
# schema version. It talks only to the compose `postgres` container from
# docker-compose.yml, never to a DATABASE_URL, so it can't reach a remote
# database. There is deliberately no TRUNCATE ... CASCADE: if any table outside
# the list still referenced these rows, Postgres refuses and nothing changes.
#
# Environment:
#   CONTAINER_ENGINE   podman|docker (else the choice `startup.sh init` saved,
#                      else auto-detect, like startup.sh).
#   POSTGRES_CONTAINER The postgres container's name (default
#                      natural-reader-postgres, docker-compose.yml's).
#   DOC_STORAGE_DIR    Where uploaded files live (also read from .env; the
#                      legacy PDF_STORAGE_DIR works too). Default ./data/pdfs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"

readonly COMPOSE_FILE="docker-compose.yml"
readonly POSTGRES_SERVICE="postgres"
readonly POSTGRES_USER="natural_reader"
readonly POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-natural-reader-postgres}"
readonly ENGINE_STATE_FILE=".local/container-engine"
readonly RUNNER_PIDFILE=".local/runner.pid"
readonly ENV_FILE=".env"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '3,24p' "$0" | sed 's/^# \{0,1\}//'; }

ASSUME_YES=0
KEEP_PROJECTS=0
CLEAR_PINS=0
for arg in "$@"; do
	case "$arg" in
		--yes|-y) ASSUME_YES=1 ;;
		--keep-projects) KEEP_PROJECTS=1 ;;
		--clear-pins) CLEAR_PINS=1 ;;
		--help|-h) usage; exit 0 ;;
		*) die "Unknown option '$arg' (see --help)." ;;
	esac
done

# Same precedence as startup.sh: $CONTAINER_ENGINE → saved choice → auto-detect.
resolve_engine() {
	local engine="${CONTAINER_ENGINE:-}"
	if [[ -z "$engine" && -f "$ENGINE_STATE_FILE" ]]; then
		engine="$(<"$ENGINE_STATE_FILE")"
	fi
	if [[ -z "$engine" ]]; then
		if command -v docker >/dev/null 2>&1; then engine="docker"
		elif command -v podman >/dev/null 2>&1; then engine="podman"
		else die "Neither docker nor podman is installed."
		fi
	fi
	[[ "$engine" == "podman" || "$engine" == "docker" ]] \
		|| die "Invalid container engine '$engine'. Use 'podman' or 'docker'."
	printf '%s' "$engine"
}

# Mirrors startup.sh's compose(): podman runs with XDG_DATA_HOME unset (snap
# podman can't find its socket otherwise) and prefers podman-compose.
compose() {
	local engine="$1"; shift
	local -a prefix=()
	[[ "$engine" == "podman" ]] && prefix=(env -u XDG_DATA_HOME)
	if [[ "$engine" == "podman" ]] && command -v podman-compose >/dev/null 2>&1; then
		"${prefix[@]}" podman-compose -f "$COMPOSE_FILE" "$@"
	elif "${prefix[@]}" "$engine" compose version >/dev/null 2>&1; then
		"${prefix[@]}" "$engine" compose -f "$COMPOSE_FILE" "$@"
	elif command -v "${engine}-compose" >/dev/null 2>&1; then
		"${prefix[@]}" "${engine}-compose" -f "$COMPOSE_FILE" "$@"
	else
		die "No compose support for $engine."
	fi
}

# Queries go straight to the container with `<engine> exec`, not through
# compose: podman-compose 1.x prints its own debug lines on every call.
engine_run() {
	if [[ "$ENGINE" == "podman" ]]; then env -u XDG_DATA_HOME podman "$@"; else docker "$@"; fi
}

# </dev/null: a query must never read the script's stdin, or it would swallow
# the answer meant for the confirmation prompt.
psql_exec() {
	engine_run exec "$POSTGRES_CONTAINER" \
		psql -U "$POSTGRES_USER" -d "$POSTGRES_USER" -v ON_ERROR_STOP=1 -qAt "$@" </dev/null
}

# The storage dir: the environment wins, then .env, then the server's default.
storage_dir() {
	local dir="${DOC_STORAGE_DIR:-${PDF_STORAGE_DIR:-}}"
	if [[ -z "$dir" && -f "$ENV_FILE" ]]; then
		# DOC_ sorts before PDF_, so the new name wins when both are set.
		dir="$(grep -E '^(DOC_STORAGE_DIR|PDF_STORAGE_DIR)=' "$ENV_FILE" \
			| sort | head -n1 | cut -d= -f2- | tr -d "\"'" || true)"
	fi
	printf '%s' "${dir:-./data/pdfs}"
}

# A running backend could write rows back mid-wipe (a pipeline job finishing).
if [[ -f "$RUNNER_PIDFILE" ]] && kill -0 "$(<"$RUNNER_PIDFILE")" 2>/dev/null; then
	die "The backend is running (pid $(<"$RUNNER_PIDFILE")). Stop it first (Ctrl-C in the terminal running ./startup.sh up)."
fi

ENGINE="$(resolve_engine)"
pg_ready() {
	engine_run exec "$POSTGRES_CONTAINER" pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1
}

# startup.sh tears every container down when its backend stops, so Postgres is
# usually off here. Start just that service, and stop it again on the way out
# so the machine is left as we found it.
if ! pg_ready; then
	log "Starting the postgres container"
	if ! out="$(compose "$ENGINE" up -d "$POSTGRES_SERVICE" 2>&1)"; then
		printf '%s\n' "$out" >&2
		die "Couldn't start the postgres container."
	fi
	trap 'log "Stopping the postgres container"; compose "$ENGINE" stop "$POSTGRES_SERVICE" >/dev/null 2>&1 || true' EXIT
	for _ in $(seq 1 30); do pg_ready && break; sleep 1; done
	pg_ready || die "Postgres didn't become ready within 30 s."
fi

TABLES="'doc_chunks','doc_pages','library_entries','doc_grants','project_documents'"
if [[ "$KEEP_PROJECTS" -eq 0 ]]; then
	TABLES="$TABLES,'project_members','projects'"
fi
TABLES="$TABLES,'documents'"

# Only tables that exist: doc_grants is pre-A1, library_entries is A1+.
PRESENT="$(psql_exec -c "SELECT string_agg(quote_ident(t), ', ') FROM unnest(ARRAY[$TABLES]) AS t WHERE to_regclass('public.' || t) IS NOT NULL")"
[[ -n "$PRESENT" ]] || die "None of the library tables exist — nothing to wipe."

STORAGE="$(storage_dir)"
log "Container engine: $ENGINE"
log "Rows now:"
IFS=', ' read -r -a present_list <<< "$PRESENT"
for t in "${present_list[@]}"; do
	[[ -n "$t" ]] && printf '    %-18s %s\n' "$t" "$(psql_exec -c "SELECT count(*) FROM $t")"
done
log "Will TRUNCATE: $PRESENT"
[[ "$CLEAR_PINS" -eq 1 ]] && log "Will empty chat pins (chat_sessions.pins)"
log "Will delete the contents of: $STORAGE"

if [[ "$ASSUME_YES" -eq 0 ]]; then
	read -r -p "Type 'wipe' to continue: " answer || answer=""
	[[ "$answer" == "wipe" ]] || die "Aborted; nothing changed."
fi

SQL="BEGIN; TRUNCATE $PRESENT;"
if [[ "$CLEAR_PINS" -eq 1 ]]; then
	SQL="$SQL UPDATE chat_sessions SET pins = '[]'::jsonb WHERE pins <> '[]'::jsonb;"
fi
SQL="$SQL COMMIT;"
psql_exec -c "$SQL"
log "Database wiped."

# Delete the files only after the rows are gone, and only inside a real
# directory that isn't the filesystem root or the home directory.
if [[ -d "$STORAGE" ]]; then
	real="$(cd "$STORAGE" && pwd -P)"
	[[ "$real" != "/" && "$real" != "$HOME" ]] || die "Refusing to empty '$real'."
	find "$real" -mindepth 1 -delete
	log "Stored files removed from $real"
else
	log "No storage directory at $STORAGE; nothing to delete."
fi

log "Done. Users, sessions, tokens, chats and the schema version are untouched."
