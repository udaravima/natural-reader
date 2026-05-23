import { getBook } from '../db';
import { buildApiUrl } from '../utils/url';

/**
 * Push the raw PDF bytes for `fileName` (pulled out of IndexedDB) to the
 * backend so a docling conversion job can read them off disk. The backend
 * persists to `data/pdfs/{doc_id}.pdf` and stamps `documents.pdf_path`.
 *
 * Throws on any HTTP error so callers can show a useful toast.
 */
export async function uploadPdfBytesToBackend({
    docId,
    fileName,
    apiHost,
    apiPort,
}) {
    if (!docId || !fileName) throw new Error('Missing docId or fileName');

    const record = await getBook(fileName);
    if (!record?.data) {
        throw new Error('File is not in the local library — re-open it and try again.');
    }

    const blob = new Blob([record.data], { type: 'application/pdf' });
    const form = new FormData();
    form.append('file', blob, fileName);

    const url = buildApiUrl(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}/pdf`);
    const res = await fetch(url, { method: 'POST', body: form });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const data = await res.json();
            if (data?.detail) detail = data.detail;
        } catch {
            // non-JSON error body; keep the HTTP code.
        }
        throw new Error(detail);
    }
    return res.json();
}
