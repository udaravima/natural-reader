-- A1 final review (C1): a holding grants read access only once it is proved.
-- Before A1 the browser sent only a hash, so migration 011's backfilled
-- `upload` entries (and the shares and project placements those owners made)
-- record who *claimed* a document, not who had its bytes. Someone who
-- registered a hash they never had would otherwise read the real author's
-- text as soon as the author uploaded the file.
--
-- `verified` = this holding comes from someone who uploaded the bytes to
-- this server: an upload entry whose user sent them, a share or placement
-- made by such a user. Every row that exists when this runs came from 011's
-- backfill, so all of them start false. Unverified holdings keep reading
-- legacy content (extracted_by = 'client', today's trust); once verified
-- bytes make the content server-extracted they stop granting read until
-- their holder uploads the file (which verifies their shares and placements
-- too). See readable_docs_where in server/auth/authz.py.

ALTER TABLE library_entries   ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE project_documents ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT false;

INSERT INTO schema_migrations(version) VALUES (12) ON CONFLICT DO NOTHING;
