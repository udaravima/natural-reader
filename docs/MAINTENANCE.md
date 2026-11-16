# Maintenance

The routine jobs for a Natural Reader server run with `./startup.sh start`: the
backend in a container, with Postgres, Keycloak and SearXNG beside it. Setting
the server up the first time is [DEPLOYMENT.md](DEPLOYMENT.md).

Run the commands from the checkout. They use `podman`; with Docker, write
`docker`. From a terminal installed as a snap (VS Code's, for one), put
`env -u XDG_DATA_HOME` in front of `podman` and `./startup.sh`.

## At a glance

| Job | When | How |
| --- | --- | --- |
| [Check it's healthy](#is-it-running) | after any change, or when something seems off | `curl -s http://127.0.0.1:8000/v1/health` |
| [Survive reboots](#surviving-reboots) | once per machine | `./startup.sh enable-autostart` |
| [Apply a `.env` change](#after-changing-env) | every time you edit `.env` | `./startup.sh rebuild` |
| [Back up](#backup-and-restore) | before every upgrade, and regularly | five commands, or a nightly job |
| [Upgrade](#upgrading-to-a-new-release) | a new release | back up, check out, rebuild both halves |
| [Roll back](#rolling-back) | an upgrade went wrong | restore the backup under the old release |
| [Rotate secrets](#rotating-the-keycloak-secrets) | a realm created by v2.11.0 or earlier; a secret that may have leaked | Keycloak console, `.env`, `./startup.sh rebuild` |
| [Free disk space](#freeing-disk-space) | the disk fills up; after many rebuilds | `podman image prune` |

## Is it running?

```bash
podman ps --format '{{.Names}}\t{{.Status}}'   # natural-reader-postgres (healthy), -searxng, -keycloak, -backend
curl -s http://127.0.0.1:8000/v1/health          # {"status":"ok","model_loaded":true,"database":"ok"}
./startup.sh logs                                # follow the backend; Ctrl-C stops following, not the backend
```

- **`database: unavailable`** means the backend can't reach Postgres. It keeps
  trying in the background, so it recovers by itself once Postgres is back.
- **`status`** is read-aloud's own state (the Kokoro model), not the whole app's.
- The port is the backend's `PORT` (8000 unless you changed it).
- The log files are under `LOG_DIR` (`./logs` by default) and rotate by
  themselves: `server.log`, and `audit.log` for every change to who holds what.
- In the app, **Admin → Overview** and **Admin → Deployment** show the same
  picture, and flag a setting that needs attention.

## Surviving reboots

**On podman, a reboot leaves the containers stopped** until you run this once on
the machine:

```bash
./startup.sh enable-autostart
```

It turns on lingering for your user (your services run without anyone logged
in) and `podman-restart.service`, which starts every container whose restart
policy is `always` (all four are) when the machine boots.

- Check: `loginctl show-user "$USER" -p Linger` says `Linger=yes`, and
  `systemctl --user is-enabled podman-restart.service` says `enabled`.
- **Docker** restarts them itself once its daemon starts at boot
  (`systemctl enable docker`).
- `./startup.sh stop` removes the containers, so a deliberate stop stays stopped
  through a reboot. `./startup.sh start` brings it back.

## After changing `.env`

**Run `./startup.sh rebuild`.** The backend container reads `.env` once, when the
container is created. `podman restart` keeps the old values, and so does
`./startup.sh start` when the code hasn't changed: it makes a new container only
for a new image. `rebuild` always makes a new one. (Checked with podman-compose
1.5.0: a value changed in `.env` reached the container only when it was
recreated.)

Two exceptions:

- **Keycloak's own settings** (`KC_HOSTNAME`, `KC_PROXY_HEADERS`, `KC_DB_*`)
  belong to the Keycloak container, so recreate that one:
  `podman rm -f natural-reader-keycloak`, then `./startup.sh start`. Its realm
  and accounts live in Postgres, so nothing is lost.
- **Values Keycloak reads only once:** the two client secrets,
  `KC_BOOTSTRAP_ADMIN_PASSWORD` and `KC_SEED_USER_PASSWORD` fill the realm when
  it is first created. Changing them in `.env` later changes nothing in Keycloak;
  see [Rotating the Keycloak secrets](#rotating-the-keycloak-secrets).

## Backup and restore

**The trap: Keycloak lives in the app's database.** Its realm (accounts,
passwords, clients) is the `keycloak` schema of the `natural_reader` database,
so one dump holds both, and restoring an older dump rolls Keycloak back too.
Losing that schema without a backup is worse than it looks: a new realm gives
every person a new identity id, and the app then refuses each existing person
at sign-in with HTTP 409 "email already linked to another identity" until
their old link is cleared by hand.

What to keep, and what losing it costs:

| What | Where | Without it |
| --- | --- | --- |
| The database `natural_reader` | volume `pgdata`, container `natural-reader-postgres` | Everything: people, chats, projects, settings, documents' text and passages, and Keycloak's realm |
| Uploaded files and their pictures | `DOC_STORAGE_DIR` on the host (default `./data/pdfs`; pictures in `figures/`) | A document can't be opened in the reader, re-indexed or converted ("Upload the file again first"); its passages still answer chat questions |
| `.env` | the checkout | The secrets. A new `SESSION_SECRET` only signs everyone out; a new `OIDC_CLIENT_SECRET` must then match the one the restored realm holds, or sign-in fails |
| `CHAT_ASSISTANT_PROFILE_FILE`, `SPEECH_MODELS_FILE` | wherever `.env` points, if set | Those settings |
| Volume `cache` | models (Hugging Face, converted speech models) | Nothing: downloaded again, slowly |

The database and the files belong together: a document uploaded or deleted
between the dump and the copy leaves a row without its file, or a file nothing
points to. Stopping the backend for the minute it takes gives a matching pair.
The dump alone is consistent either way (`pg_dump` reads one snapshot).

**Backing up:**

```bash
B=~/nr-backup/$(date +%F); mkdir -p "$B"
podman stop natural-reader-backend          # a quiet minute: files and rows match
podman exec natural-reader-postgres pg_dump -U natural_reader -Fc natural_reader > "$B/natural_reader.dump"
rsync -a data/pdfs/ "$B/pdfs/"              # your DOC_STORAGE_DIR
install -m 600 .env "$B/env"                # secrets: keep this copy private
podman start natural-reader-backend
```

Keep a copy off the machine; a backup on the same disk goes with it. A backup
also holds the secrets of its day: restore one taken before you rotated a
secret and the old secret comes back, so rotate again after restoring it.

### Every night, by itself

Save this as `~/bin/nr-backup.sh`, set the `cd` line to your checkout, and
`chmod +x` it:

```bash
#!/usr/bin/env bash
# One backup of Natural Reader into ~/nr-backup/<date>.
set -euo pipefail
cd /path/to/natural-reader
B=~/nr-backup/$(date +%F); mkdir -p "$B"
podman stop natural-reader-backend >/dev/null
trap 'podman start natural-reader-backend >/dev/null' EXIT   # started again even if a step fails
podman exec natural-reader-postgres pg_dump -U natural_reader -Fc natural_reader > "$B/natural_reader.dump"
rsync -a data/pdfs/ "$B/pdfs/"
install -m 600 .env "$B/env"
echo "$(date '+%F %T') backup OK: $(du -sh "$B" | cut -f1) in $B"
```

Then `crontab -e` and add a line that runs it at 03:30:

```text
30 3 * * * $HOME/bin/nr-backup.sh >> $HOME/nr-backup/backup.log 2>&1
```

- The app is down for the minute the copy takes.
- Cron runs without a login session, so rootless podman needs lingering on
  ([Surviving reboots](#surviving-reboots) turns it on).
- Each night adds a folder: the dump plus a full copy of your files. Delete old
  ones yourself, keeping a few, and copy one off the machine now and then.
- **Not checked:** the script running under cron with nobody logged in. Run it
  once by hand, look at the folder, then check `backup.log` the morning after.

**Restoring** (the containers exist and Postgres is running; on a new machine,
start the stack once first so they do):

```bash
podman stop natural-reader-backend natural-reader-keycloak
podman exec natural-reader-postgres dropdb -U natural_reader --force natural_reader
podman exec natural-reader-postgres createdb -U natural_reader natural_reader
podman exec -i natural-reader-postgres pg_restore -U natural_reader -d natural_reader --no-owner < "$B/natural_reader.dump"
rsync -a --delete "$B/pdfs/" data/pdfs/
podman start natural-reader-keycloak natural-reader-backend
```

- **`-i` on the `pg_restore` line is not optional.** Without it podman passes no
  input, and `pg_restore` stops with "input file is too short".
- `dropdb` deletes the current database. Make a fresh dump of it first if there
  is any chance you want it back.
- A dump from an older version restores fine: the backend applies the newer
  migrations when it starts. A dump from a **newer** version doesn't belong
  under older code; see [Rolling back](#rolling-back).

**What has been checked** (2026-11-14, on a scratch `pgvector/pgvector:pg16`
container): a fully migrated database with a `keycloak` schema, dumped and
restored with these commands, came back with the same tables in both schemas,
all 26 migrations recorded, the `vector` extension and its 56 indexes, and
vector ordering unchanged; the backend's database layer then started on it
without applying a migration. **Not checked:** a restore on a real deployment,
in particular Keycloak signing people in against a restored realm.

## Upgrading to a new release

1. **Read the release notes,** `docs/Release_notes/RELEASE_NOTES_vX.Y.Z.md`,
   section **Upgrading**: the migrations it runs, new settings, anything to do
   by hand. Skipping releases, read each one's.
2. **Back up** ([above](#backup-and-restore)).
3. **Get the code:**

   ```bash
   git fetch --tags
   git checkout vX.Y.Z
   ```

4. **Rebuild both halves:**

   ```bash
   ./startup.sh rebuild        # the backend; new migrations run when it starts
   npm ci && npm run build     # the web app, into dist/ (Node 22.13 or newer)
   ```

   If your web server serves a copy of `dist/` rather than the checkout's own,
   copy it there, for example
   `sudo rsync -a --delete dist/ /var/www/chat.example.com/`. When the notes say
   Keycloak changed, also `podman rm -f natural-reader-keycloak` and
   `./startup.sh start`: `rebuild` only makes a new backend.
5. **Check:** `./startup.sh logs` shows `Applying migration …` for each new
   migration, then `Application startup complete`; the health check says `ok`;
   after a reload, **Settings** ends with the new release number.

## Rolling back

**The simple way back is the backup you took before upgrading,** restored under
the release you came from. Start only the old backend on the restored database,
never the new one: a new backend would migrate it forward again.

```bash
B=~/nr-backup/2026-11-16                   # the backup taken before the upgrade
git checkout vOLD                          # the release you came from
podman stop natural-reader-backend
podman rm -f natural-reader-keycloak       # start makes it again, as the old release has it
podman exec natural-reader-postgres dropdb -U natural_reader --force natural_reader
podman exec natural-reader-postgres createdb -U natural_reader natural_reader
podman exec -i natural-reader-postgres pg_restore -U natural_reader -d natural_reader --no-owner < "$B/natural_reader.dump"
rsync -a --delete "$B/pdfs/" data/pdfs/
./startup.sh start                         # Keycloak, then the old backend, rebuilt, on the old database
npm ci && npm run build
```

- Removing Keycloak's container loses nothing (its realm is in the database you
  just restored). It matters when the release changed Keycloak's version: a
  newer Keycloak would upgrade the restored realm, and that can't be undone.
- Anything written since the backup is lost.
- Without a backup, each migration's header has a Down block; the release notes
  say which to run, in what order, and what goes with them.
- **Not checked:** a rollback end to end on a real deployment.

## Rotating the Keycloak secrets

**Do this if your realm was created by v2.11.0 or earlier.** Until then
`deploy/keycloak/realm-export.json` contained working values: the secret of the
sign-in client, the secret of `natural-reader-admin` (a service account that can
create users and reset anyone's password), the password of the seeded
`admin-user` (an app admin), and compose set Keycloak's master admin to
`admin`/`admin`. They are public. Keycloak's token endpoint has to be reachable
for sign-in to work, so anyone who can reach it can use them.

The backend tells you: at start it logs an error naming each variable that
still holds a published value, and **Admin → Deployment** shows "set, but to a
published default: rotate it".

1. **The two client secrets.** In the Keycloak admin console: realm
   `natural-reader` → Clients → `natural-reader` → Credentials → **Regenerate**;
   the same for `natural-reader-admin`. Put the new values in `.env` as
   `OIDC_CLIENT_SECRET` and `KC_ADMIN_CLIENT_SECRET`, then `./startup.sh rebuild`.
   A restart is not enough ([After changing `.env`](#after-changing-env)).
   Sign-in fails between the two steps, so do them together.
2. **`admin-user`.** Once your own account is an admin, delete it (Users →
   `admin-user` → Delete), or give it a new password under Credentials.
3. **The master admin.** Sign in to the admin console as `admin`, then change the
   password (top right → Manage account), or create a new admin in the `master`
   realm and delete `admin`.
4. **Check:** `./startup.sh logs` shows no error naming a published secret, and
   **Admin → People** loads (it uses the second secret).

A fresh install needs none of this: `./startup.sh` generates all four values into
`.env`, and the realm file only holds `${PLACEHOLDERS}` that Keycloak fills from
them when it first creates the realm. They are read once. Changing them in `.env`
later does not change an existing realm; use the steps above.

**Rotate the same way whenever a secret may have leaked** (a copy of `.env` sent
somewhere, a backup left on a shared disk). The app's own `SESSION_SECRET` signs
its session cookies: a new value (`openssl rand -hex 32`, then
`./startup.sh rebuild`) signs everyone out and changes nothing else.

## Freeing disk space

**Each `rebuild` leaves the previous backend image behind,** untagged
("dangling"): about 3 GB each with Docling in the image. To see and delete them:

```bash
podman system df                     # how much images, containers and volumes take
podman images -f dangling=true       # the leftover backend images
podman image prune                   # deletes dangling images; asks first
```

- **Never `podman volume prune` or `podman system prune --volumes` after
  `./startup.sh stop`.** `stop` removes the containers, so the `pgdata` volume
  counts as unused and is deleted with the rest: that is your database.
- **`podman image prune -a`** deletes every image no container uses. After a
  `stop` that is all of them, including the backend's; they come back, slowly,
  by pulling and rebuilding. Plain `prune` is enough.
- Log files rotate by themselves. Chat models live in Ollama, outside this app
  (`ollama list`, `ollama rm <model>`).
