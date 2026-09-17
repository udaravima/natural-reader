-- Runs only when the Postgres data volume is first initialized
-- (docker-entrypoint-initdb.d semantics). Keycloak (docker-compose.yml) stores
-- its realm in this schema; keeping it alongside the app's tables in the same
-- database avoids a second container/database for local dev.
--
-- On an ALREADY-initialized volume this script never runs — create the schema
-- once by hand instead:
--   PGPASSWORD=natural_reader psql -h 127.0.0.1 -p 5433 -U natural_reader \
--     -d natural_reader -c 'CREATE SCHEMA IF NOT EXISTS keycloak'
CREATE SCHEMA IF NOT EXISTS keycloak;
