import { apiFetch } from '../utils/apiFetch';
import { describeRefusal } from './apiErrors';
import { detectFileType } from '../db';

// What GET /v1/docs/{id}/file serves each stored type as, and the extension
// that makes detectFileType (src/db.js) agree with it. Anything else is
// treated as a PDF, as detectFileType does.
const FILE_TYPES = {
    'application/pdf': { kind: 'pdf', ext: '.pdf' },
    'text/markdown': { kind: 'markdown', ext: '.md' },
    'text/plain': { kind: 'text', ext: '.txt' },
};

/**
 * Fetch a server document's stored bytes (GET /v1/docs/{id}/file) as a File
 * the reader can open exactly like one the user picked: `name` is the name
 * the caller sees for it in the Library (the server names the download the
 * same way). The stored type wins over the name: a PDF someone named
 * "notes.md" is opened as "notes.md.pdf", never read as Markdown. Throws an
 * Error whose message is a notice a person can act on (404: no access;
 * 409 bytes_missing: nobody has uploaded the bytes).
 */
export async function fetchDocFile(apiHost, apiPort, docId, name) {
    const res = await apiFetch(apiHost, apiPort, `/v1/docs/${docId}/file`);
    if (!res.ok) throw new Error(await describeRefusal(res));
    const mime = (res.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
    const bytes = await res.arrayBuffer();
    const type = FILE_TYPES[mime] ? mime : 'application/pdf';
    const { kind, ext } = FILE_TYPES[type];
    const fileName = detectFileType({ name, type }) === kind ? name : `${name}${ext}`;
    return new File([bytes], fileName, { type });
}
