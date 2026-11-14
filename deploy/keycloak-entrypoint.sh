#!/bin/bash
# Entrypoint of the compose Keycloak (docker-compose.yml mounts this file; it is not in the import folder,
# whose every file Keycloak reads as a realm).
#
# deploy/keycloak/realm-export.json holds no secrets: the two client secrets and the seeded user's password
# are ${PLACEHOLDERS} that Keycloak fills from this environment when it first creates the realm. A
# placeholder with no value would be imported as its own literal text, a secret anyone can read in the
# repository. So anything unset gets a random value here: that client or login then simply doesn't work
# until the deployer sets the variable in .env, which is the safe way to fail.
#
# An existing realm is never re-imported, so none of this changes a deployment that is already running.
set -u

random_secret() {   # 64 hex characters from the kernel, with bash alone (the image has no openssl)
	local a b
	read -r a </proc/sys/kernel/random/uuid
	read -r b </proc/sys/kernel/random/uuid
	printf '%s%s' "${a//-/}" "${b//-/}"
}

# Keycloak refuses an empty KC_PROXY_HEADERS and restarts forever; compose passes it empty unless .env sets it.
[ -n "${KC_PROXY_HEADERS:-}" ] || unset KC_PROXY_HEADERS

for name in OIDC_CLIENT_SECRET KC_ADMIN_CLIENT_SECRET KC_SEED_USER_PASSWORD KC_BOOTSTRAP_ADMIN_PASSWORD; do
	if [ -z "${!name:-}" ]; then
		export "$name=$(random_secret)"
		echo "natural-reader: $name is not set in .env, so a realm created by this start gets a random value for it that nobody knows. Set it in .env before the first start (an existing realm is unaffected)." >&2
	fi
done

exec "${KC_BIN:-/opt/keycloak/bin/kc.sh}" "$@"
