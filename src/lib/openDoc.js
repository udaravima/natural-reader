/**
 * Opening a server document in the reader, from the Library (Task 5) or a
 * chat citation (Task 6). Pure orchestration over what App hands in, so the
 * flow is tested without mounting App:
 *   fetchDocFile(docId, name) → File     (src/lib/serverDocFile.js)
 *   processFile(file) → { numPages } | null   (usePdfEngine)
 *   showToast(message, ms), showReader(), goToPage(n)
 */

/**
 * Fetch the stored bytes and open them like a picked file: processFile saves
 * them to this user's local library and loads them, and the bytes hash to
 * the doc id, so Index and chat find the server copy. Shows the reader only
 * once it's open; a refusal (404, 409 bytes_missing) or a file the reader
 * can't load is a toast, and the view stays where it was. Resolves
 * `{ numPages }` or null.
 */
export async function openServerDoc(deps, docId, name) {
    try {
        const file = await deps.fetchDocFile(docId, name);
        const opened = await deps.processFile(file);
        if (!opened) throw new Error("the reader couldn't load this file.");
        deps.showReader();
        return opened;
    } catch (e) {
        deps.showToast(`Could not open "${name}": ${e.message}`, 5000);
        return null;
    }
}

const clampPage = (page, numPages) => Math.max(1, numPages ? Math.min(page, numPages) : page);

/**
 * A "(page N)" citation was clicked. The cited document open already
 * (`openDocId`, the open document's hash, is its doc id): show the reader at
 * that page. Otherwise open it through the Library route first and go to the
 * page once it's loaded — after its saved reading position was applied.
 */
export async function openCitation(deps, { docId, page, docName, openDocId, numPages }) {
    if (openDocId && openDocId === docId) {
        deps.showReader();
        deps.goToPage(clampPage(page, numPages));
        return;
    }
    const opened = await openServerDoc(deps, docId, docName || 'Cited document');
    if (opened) deps.goToPage(clampPage(page, opened.numPages));
}
