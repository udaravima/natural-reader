/**
 * Page citations in assistant replies (Task 6). A reply that used a document
 * cites it as "page N" or "(page N)"; those become buttons that open that
 * document at that page. The document comes from what the server saved with
 * the reply, never from the reply text: the prefetch note in
 * `docContext.notes`, or the summary of a successful call to a tool that
 * read a document (it saves `docId`): `search_documents`, or
 * `search_document` in chats saved before v2.3.
 */

/** The document a reply's page citations refer to: `{ docId, docName }` or null. */
export function citationDoc(message) {
    const note = (message?.docContext?.notes || []).find((n) => n?.kind === 'prefetch' && n.docId);
    if (note) return { docId: note.docId, docName: note.docName };
    const call = (message?.toolCalls || []).find(
        (tc) => tc?.result_summary?.ok && tc.result_summary.docId);
    if (call) return { docId: call.result_summary.docId, docName: call.result_summary.docName };
    return null;
}

// "(page 4)" as a whole, else a bare "page 4". Case-insensitive ("Page 4").
const CITATION = /\(page\s+(\d+)\)|\bpage\s+(\d+)\b/gi;

// Nodes whose text isn't prose: code, and links the model wrote itself.
const SKIP = new Set(['link', 'linkReference', 'inlineCode', 'code', 'html', 'definition']);

function splitText(node) {
    const out = [];
    let last = 0;
    for (const m of node.value.matchAll(CITATION)) {
        const page = Number(m[1] || m[2]);
        if (page < 1) continue; // "page 0" is no page: left as text
        if (m.index > last) out.push({ type: 'text', value: node.value.slice(last, m.index) });
        out.push({
            type: 'link',
            url: `#page-${page}`,
            children: [{ type: 'text', value: m[0] }],
            // Markdown can't set attributes, so a link the model writes
            // never carries this — only citations found here do.
            data: { hProperties: { 'data-cite-page': page } },
        });
        last = m.index + m[0].length;
    }
    if (out.length === 0) return [node];
    if (last < node.value.length) out.push({ type: 'text', value: node.value.slice(last) });
    return out;
}

function walk(node) {
    if (!Array.isArray(node.children) || SKIP.has(node.type)) return;
    node.children = node.children.flatMap((child) => {
        if (child.type === 'text') return splitText(child);
        walk(child);
        return [child];
    });
}

/** remark plugin: marks page citations as links carrying `data-cite-page`. */
export function remarkCitations() {
    return (tree) => walk(tree);
}
