import { apiFetch } from '../utils/apiFetch';
import { describeRefusal, noticeFor } from './apiErrors';

/**
 * Parse a free-text tags field (comma-separated) into a clean array: trimmed,
 * empty entries dropped, duplicates removed (first occurrence wins). Used by
 * the optional project/tags picker on the register/upload surface.
 */
export function parseTagsInput(text) {
    if (!text) return [];
    const seen = new Set();
    const out = [];
    for (const raw of text.split(',')) {
        const t = raw.trim();
        if (!t || seen.has(t)) continue;
        seen.add(t);
        out.push(t);
    }
    return out;
}

/**
 * Register a document with the backend by uploading its bytes
 * (`POST /v1/docs`, multipart). The server hashes the bytes, dedupes against
 * existing content, and — for new content — kicks off extraction/embedding in
 * the background. Tags travel with the upload; a project link, when asked
 * for, follows as a separate request once the upload has succeeded.
 *
 * Both `handleIndexDocument` and the convert flow in App.jsx call this before
 * doing their own work (index kick-off, or convert). This helper is the
 * single place that sequencing lives, so it isn't duplicated — and can't
 * drift — across the two call sites.
 *
 * Throws `Error(<notice>)` on a refused upload — the notice is the mapped,
 * human-readable text from `describeRefusal`, ready to show in a toast.
 *
 * Returns `{docId, dedup, state}` from the server's response. `docId` is the
 * server's hash, which is authoritative even if it differs from the client's
 * own guess (a lying or stale `clientDocId`).
 */
export async function registerDocument({
    apiHost, apiPort, file, fileName, clientDocId, projectId, tags, linkProject = true,
}) {
    const form = new FormData();
    form.append('file', file, fileName);
    form.append('file_name', fileName);
    if (clientDocId) form.append('client_doc_id', clientDocId);
    for (const t of tags || []) form.append('tags', t);
    const res = await apiFetch(apiHost, apiPort, '/v1/docs', { method: 'POST', body: form });
    if (!res.ok) throw new Error(await describeRefusal(res));
    const body = await res.json();
    const result = { docId: body.doc_id, dedup: !!body.dedup, state: body.state };
    if (projectId && linkProject) {
        await linkDocToProject({ apiHost, apiPort, projectId, docId: result.docId });
    }
    return result;
}

// Fail-soft: the document is in the library either way; a failed link is
// logged and must not block the indexing/conversion that follows.
export async function linkDocToProject({ apiHost, apiPort, projectId, docId }) {
    try {
        const res = await apiFetch(
            apiHost, apiPort,
            `/v1/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(docId)}`,
            { method: 'PUT' },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
        console.error('Doc project link failed:', e);
    }
}

/**
 * Ask the server to re-index an indexed doc from its stored bytes
 * (`POST /v1/docs/{id}/index`). Returns one of:
 * - `{ started: true }` — the job is running; poll the doc.
 * - `{ needsUpload: true }` — 409 `bytes_missing`: the server has no stored
 *   bytes to rebuild from. Most docs indexed before A1 are like that (the
 *   browser sent chunks, never the file), so the caller uploads the local
 *   file instead; the server restores the bytes and, for legacy content,
 *   re-extracts from them.
 * - `{ notice }` — any other refusal, as a toast-ready notice.
 */
export async function requestReindex({ apiHost, apiPort, docId }) {
    const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}/index`, { method: 'POST' });
    if (res.ok) return { started: true };
    let detail = null;
    try {
        detail = (await res.json())?.detail ?? null;
    } catch {
        // non-JSON body — fall back to the status
    }
    if (res.status === 409 && detail?.error === 'bytes_missing') return { needsUpload: true };
    return { notice: noticeFor(res.status, detail) };
}

/**
 * Delete a doc's converted Markdown (`DELETE /v1/docs/{id}/markdown`). The
 * server then falls back to its own extraction from the stored bytes, so the
 * doc goes back through extracting → indexed. Returns `{ state }` (the state
 * the server moved to); throws `Error(<notice>)` on a refusal.
 */
export async function deleteConvertedMarkdown({ apiHost, apiPort, docId }) {
    const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}/markdown`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await describeRefusal(res));
    const body = await res.json();
    return { state: body.state };
}
