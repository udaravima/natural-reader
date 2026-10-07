-- A0 (spec docs/superpowers/specs/2026-09-25-projects-roles-directory-design.md §3):
-- GitLab-style roles on memberships, people's names, per-user project limits,
-- and the project activity feed. Membership becomes the ONLY source of
-- project access; a project's creator is history, not access.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS username      TEXT,
    ADD COLUMN IF NOT EXISTS first_name    TEXT,
    ADD COLUMN IF NOT EXISTS last_name     TEXT,
    ADD COLUMN IF NOT EXISTS project_limit INTEGER CHECK (project_limit >= 0);

ALTER TABLE project_members
    ADD COLUMN IF NOT EXISTS role      TEXT NOT NULL DEFAULT 'contributor'
        CHECK (role IN ('reader', 'contributor', 'maintainer', 'owner')),
    ADD COLUMN IF NOT EXISTS added_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS added_via TEXT NOT NULL DEFAULT 'member'
        CHECK (added_via IN ('member', 'admin'));

-- Every project's owner becomes an Owner member; an existing member row is raised.
INSERT INTO project_members (project_id, user_id, role, added_by, added_at)
SELECT id, owner_user_id, 'owner', owner_user_id, created_at FROM projects
ON CONFLICT (project_id, user_id) DO UPDATE SET role = 'owner';

-- The default existed for the backfill; from now on every row names its role.
ALTER TABLE project_members ALTER COLUMN role DROP DEFAULT;

ALTER TABLE projects RENAME COLUMN owner_user_id TO created_by;
ALTER TABLE projects ALTER COLUMN created_by DROP NOT NULL;
ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_owner_user_id_fkey;
ALTER TABLE projects ADD CONSTRAINT projects_created_by_fkey
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
-- Serves the per-user project-limit count.
CREATE INDEX IF NOT EXISTS projects_created_by_idx ON projects(created_by);

-- One row per change, written in the same transaction as the change (§3.2).
-- `kind` is validated in the app (server/services/project_events.py KINDS),
-- so later features add kinds without a migration. `doc_id` has no FK so
-- history survives the document.
CREATE TABLE IF NOT EXISTS project_events (
    id              BIGSERIAL PRIMARY KEY,
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL,
    subject_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    doc_id          TEXT,
    details         JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS project_events_feed_idx ON project_events(project_id, id DESC);
-- Serves the retention purge (PROJECT_EVENTS_RETENTION_DAYS).
CREATE INDEX IF NOT EXISTS project_events_at_idx ON project_events(at);

-- So an existing project's Activity tab isn't empty after the upgrade.
INSERT INTO project_events (project_id, at, actor_user_id, kind, details)
SELECT id, created_at, created_by, 'project.created',
       jsonb_build_object('name', name, 'backfilled', true)
FROM projects;

INSERT INTO schema_migrations(version) VALUES (17) ON CONFLICT DO NOTHING;
