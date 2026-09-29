import { apiFetch } from '../utils/apiFetch';
import { describeRefusal } from './apiErrors';

// The file types GET /v1/docs/{id}/file serves, as the MIME a File needs so
// detectFileType (src/db.js) reads it right even when the name has no
// extension. Anything else is treated as a PDF, as detectFileType does.
const FILE_TYPES = ['application/pdf', 'text/markdown', 'text/plain'];

/**
 * Fetch a server document's stored bytes (GET /v1/docs/{id}/file) as a File
 * the reader can open exactly like one the user picked: `name` is the name
 * the caller sees for it in the Library (the server names the download the
 * same way). Throws an Error whose message is a notice a person can act on
 * (404: no access; 409 bytes_missing: nobody has uploaded the bytes).
 */
export async function fetchDocFile(apiHost, apiPort, docId, name) {
    const res = await apiFetch(apiHost, apiPort, `/v1/docs/${docId}/file`);
    if (!res.ok) throw new Error(await describeRefusal(res));
    const mime = (res.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
    const bytes = await res.arrayBuffer();
    return new File([bytes], name, { type: FILE_TYPES.includes(mime) ? mime : 'application/pdf' });
}
