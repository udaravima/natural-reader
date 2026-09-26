import { apiFetch } from '../utils/apiFetch';

/**
 * Doc states the server moves through on its own (A1 spec §4 States) while a
 * poll loop below should keep going. Anything else — a terminal state, or a
 * state neither poller recognizes — ends the loop.
 */
const PROCESSING_STATES = new Set(['stored', 'extracting', 'extracted', 'indexing']);

const POLL_MS = 2000;
const MAX_INDEX_POLLS = 300; // ~10 minutes
const MAX_CONVERT_POLLS = 600; // ~20 minutes — docling on big PDFs is slow.

/**
 * Poll `/v1/docs/{apiDocId}` until indexing settles (indexed/failed) or an
 * unrecognized state appears, reporting progress to `setDocIndexByDocId` and
 * `showToast` as it goes.
 *
 * `apiDocId` and `stateKey` are deliberately separate: `apiDocId` is the
 * server's hash, which is authoritative for every network call, but the UI
 * (IndexButton) reads its state from `docIndexByDocId[currentDocId]`, where
 * `currentDocId` is the *local* hash the flow started with and is never
 * repointed to the server's id. If the two ever disagree — the server hashed
 * the same bytes differently, or a stale client guess — writing progress
 * under `apiDocId` would land it on a key nothing reads, and the button
 * would silently freeze on "Uploading"/"Extracting" forever. Writing under
 * `stateKey` instead keeps the button live regardless.
 *
 * `pollMs`/`maxPolls` are overridable so tests don't have to wait for real
 * timers.
 */
export async function pollIndexUntilSettled({
    apiDocId, stateKey, apiHost, apiPort, setDocIndexByDocId, showToast,
    pollMs = POLL_MS, maxPolls = MAX_INDEX_POLLS,
}) {
    for (let i = 0; i < maxPolls; i++) {
        await new Promise((r) => setTimeout(r, pollMs));
        try {
            const sRes = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(apiDocId)}`);
            if (!sRes.ok) continue;
            const sData = await sRes.json();
            setDocIndexByDocId((prev) => ({
                ...prev,
                [stateKey]: { state: sData.state, chunkCount: sData.chunk_count, embeddedCount: sData.embedded_count },
            }));
            if (sData.state === 'indexed') { showToast(`Indexed ${sData.embedded_count} chunks.`, 3000); return; }
            if (sData.state === 'failed') { showToast(`Indexing failed: ${sData.error_message || 'unknown error'}`, 6000); return; }
            if (!PROCESSING_STATES.has(sData.state)) return;
        } catch {
            // transient backend hiccup — keep polling
        }
    }
    showToast('Indexing is taking unusually long — check the server logs.', 6000);
}

/**
 * Poll `/v1/docs/{apiDocId}` until a docling conversion (and its chained
 * indexing) settles, reporting progress to `setDocConvertByDocId`,
 * `setDocIndexByDocId` and — on success — `setDocViewByDocId` (switches the
 * reader to the converted MD view).
 *
 * Same `apiDocId`/`stateKey` split as `pollIndexUntilSettled`, and for the
 * same reason: the convert button reads `docConvertByDocId[currentDocId]`
 * (the local hash), so progress has to be written under `stateKey`, not the
 * server's `apiDocId`, or it silently stops updating on a hash mismatch.
 */
export async function pollConvertUntilSettled({
    apiDocId, stateKey, apiHost, apiPort,
    setDocConvertByDocId, setDocIndexByDocId, setDocViewByDocId, showToast,
    pollMs = POLL_MS, maxPolls = MAX_CONVERT_POLLS,
}) {
    for (let i = 0; i < maxPolls; i++) {
        await new Promise((r) => setTimeout(r, pollMs));
        try {
            const sRes = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(apiDocId)}`);
            if (!sRes.ok) continue;
            const sData = await sRes.json();
            setDocConvertByDocId((prev) => ({
                ...prev,
                [stateKey]: {
                    ...(prev[stateKey] || {}),
                    state: sData.conversion_state || 'idle',
                    pageCount: sData.converted_page_count || 0,
                    error: sData.conversion_error || null,
                    options: sData.conversion_options || prev[stateKey]?.options || null,
                    hasPdf: !!sData.has_pdf,
                },
            }));
            setDocIndexByDocId((prev) => ({
                ...prev,
                [stateKey]: { state: sData.state || 'idle', chunkCount: sData.chunk_count, embeddedCount: sData.embedded_count },
            }));
            if (sData.conversion_state === 'conversion_failed') {
                showToast(`Conversion failed: ${sData.conversion_error || 'unknown error'}`, 6000);
                return;
            }
            if (sData.conversion_state === 'converted' && sData.state === 'indexed') {
                showToast(`Converted ${sData.converted_page_count} pages.`, 3000);
                setDocViewByDocId((prev) => ({ ...prev, [stateKey]: 'md' }));
                return;
            }
            if (sData.conversion_state === 'converted' && sData.state === 'failed') {
                // Conversion worked but downstream embedding failed.
                showToast(`Conversion done, but indexing failed: ${sData.error_message || 'unknown'}`, 6000);
                return;
            }
        } catch {
            // Transient hiccup — keep polling.
        }
    }
    showToast('Conversion is taking unusually long — check the server logs.', 6000);
}
