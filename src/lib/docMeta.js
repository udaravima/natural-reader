import { apiFetch } from '../utils/apiFetch';

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
 * Register a document with the backend (`POST /v1/docs`), then — only if the
 * caller chose a project and/or at least one tag — issue follow-up requests:
 * `PATCH /v1/docs/{id}` for tags, and `PUT /v1/projects/{id}/docs/{id}` for the link.
 *
 * Both `handleIndexDocument` and the convert flow in App.jsx register a doc
 * before doing their own work (chunk/index, or upload-bytes/convert). This
 * helper is the single place that sequencing lives, so it isn't duplicated
 * — and can't drift — across the two call sites.
 *
 * Sends no follow-up request at all when neither `projectId` nor `tags` is
 * set — this is the plain upload/register path and it must behave exactly as
 * before this feature existed.
 *
 * Throws on a failed register call (same contract the two call sites had
 * inline before). A failed tag PATCH or project link is logged but does not throw —
 * the doc is registered either way, and losing the project/tag attachment
 * shouldn't block the indexing/conversion that immediately follows.
 */
export async function registerDocument({
    apiHost, apiPort, docId, fileName, fileType, sizeBytes, pageCount,
    projectId, tags,
}) {
    const res = await apiFetch(apiHost, apiPort, '/v1/docs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            doc_id: docId,
            file_name: fileName,
            file_type: fileType,
            size_bytes: sizeBytes,
            page_count: pageCount,
        }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const hasProject = !!projectId;
    const hasTags = Array.isArray(tags) && tags.length > 0;
    // Two independent follow-ups, each fail-soft: a failed tag PATCH must not
    // skip the project link, and neither may block the indexing/conversion
    // that runs right after register.
    if (hasTags) {
        try {
            const patchRes = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tags }),
            });
            if (!patchRes.ok) throw new Error(`HTTP ${patchRes.status}`);
        } catch (e) {
            console.error('Doc tags PATCH failed:', e);
        }
    }
    if (hasProject) {
        try {
            const linkRes = await apiFetch(
                apiHost, apiPort,
                `/v1/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(docId)}`,
                { method: 'PUT' },
            );
            if (!linkRes.ok) throw new Error(`HTTP ${linkRes.status}`);
        } catch (e) {
            console.error('Doc project link failed:', e);
        }
    }

    return res;
}
