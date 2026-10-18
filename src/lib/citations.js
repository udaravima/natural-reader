/**
 * Page citations in assistant replies (Task 6). A reply that used a document
 * cites it as "page N" or "(page N)"; those become buttons that open that
 * document at that page. The document comes from what the server saved with
 * the reply, never from the reply text: the prefetch note in
 * `docContext.notes`, or the summary of a successful call to a tool that
 * read a document (it saves `docId`): `search_documents`, or
 * `search_document` in chats saved before v2.3; or the reply's source chips
 * note (v2.4).
 *
 * A project reply (C2.1) cites several documents, by name: "(Guide.md, page
 * 7)", joined "(Guide.md, page 3; Notes.txt, pages 4-5)". The names that count
 * are the ones in the reply's own `scope` note, matched case-insensitively,
 * longest first; a name that isn't there stays plain text. A bare "(page N)"
 * still links, but only to a document `citationDocs` can name as the one the
 * reply read.
 */

/** The document a reply's page citations refer to: `{ docId, docName }` or null. */
export function citationDoc(message) {
    const note = (message?.docContext?.notes || []).find((n) => n?.kind === 'prefetch' && n.docId);
    if (note) return { docId: note.docId, docName: note.docName };
    const call = (message?.toolCalls || []).find(
        (tc) => tc?.result_summary?.ok && tc.result_summary.docId);
    if (call) return { docId: call.result_summary.docId, docName: call.result_summary.docName };
    const sources = (message?.docContext?.notes || []).find((n) => n?.kind === 'sources' && n.docId);
    if (sources) return { docId: sources.docId, docName: sources.docName };
    return null;
}

/**
 * What a reply's citations resolve against: `names` are the documents of the
 * reply's own scope note (a project reply), for "(Name, page N)"; `single` is
 * the one document a bare "(page N)" means: today's `citationDoc`, else the
 * only document a project reply's evidence (prefetch, sources, tool calls)
 * touches. Two documents or none: `single` is null, so a bare page stays text.
 * Never the session's current scope, never the open document: replies are
 * kept, and the scope moves on.
 */
export function citationDocs(message) {
    const notes = message?.docContext?.notes || [];
    const scope = notes.find((n) => n?.kind === 'scope');
    const names = (Array.isArray(scope?.documents) ? scope.documents : [])
        .filter((d) => d?.name && d.docId)
        .map((d) => ({ name: d.name, docId: d.docId }));
    return { names, single: citationDoc(message) || soleEvidence(message, names) };
}

function soleEvidence(message, names) {
    const found = new Map(); // docId -> docName ('' while unknown)
    const add = (d) => {
        if (d?.docId && (d.docName || !found.has(d.docId))) found.set(d.docId, d.docName || '');
    };
    for (const note of message?.docContext?.notes || []) {
        if (note?.kind === 'prefetch' || note?.kind === 'sources') (note.documents || []).forEach(add);
    }
    for (const call of message?.toolCalls || []) {
        const r = call?.result_summary;
        if (!r?.ok) continue;
        (r.documents || []).forEach(add); // extract_from_documents
        add(r.document); // read_document_pages, document_outline
        (r.passages || []).forEach(add); // search_documents: a docId per passage
    }
    if (found.size !== 1) return null;
    const [[docId, docName]] = found;
    return { docId, docName: docName || names.find((n) => n.docId === docId)?.name };
}

// The ways a page is cited, as the server reads them (chat/sources.py): "page
// 4", "pages 4-5", "pages 4 and 6", "pages 4, 6 & 8", "p. 7", "pp. 4-5". A range
// links its first page, whatever its length, and stays one link ("4-5" is a
// single click); the pages of a list are one link each.
const KEYWORD = String.raw`(?:pages?\s+|pp?\.\s*)`;
const ITEM = String.raw`\d{1,6}(?:\s*[-–—]\s*\d{1,6})?`;
const LIST = String.raw`${ITEM}(?:\s*(?:,\s*and\b|,|\band\b|&)\s*${ITEM})*`;
const ITEM_RE = /(\d+)(?:\s*[-–—]\s*(\d+))?/g;
const BARE_PART = new RegExp(`^(${KEYWORD})(${LIST})$`, 'i');
// The text before a "page N" ends in "(…, ": the page of a citation whose name
// isn't one of the scope's. Only the last stretch is looked at, which bounds
// the scan.
const OPEN_NAMED = /\((?:[^()]|\([^()]*\))*,\s*$/;
const LOOKBACK = 200;

const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** A list of pages ("4-5 and 8") as `[{at, text, page}]`, or null if one starts below page 1. */
function pagesOf(list) {
    const items = [...list.matchAll(ITEM_RE)].map((m) => ({ at: m.index, text: m[0], page: Number(m[1]) }));
    return items.every((item) => item.page >= 1) ? items : null; // "page 0" is no page
}

/**
 * One cited part, "Guide.md, pages 4 and 6", as pieces of text, each either
 * plain or a link to a page: the first link carries everything up to its page
 * ("Guide.md, pages 4"), the later ones just their number ("6"). Null if the
 * pages aren't any.
 */
function partPieces(lead, list) {
    const items = pagesOf(list);
    if (!items) return null;
    const pieces = [{ value: lead + items[0].text, page: items[0].page }];
    let at = items[0].at + items[0].text.length;
    for (const item of items.slice(1)) {
        pieces.push({ value: list.slice(at, item.at) }, { value: item.text, page: item.page });
        at = item.at + item.text.length;
    }
    return pieces;
}

/** What the plugin matches for these names: the regexp, and which document a name means. */
function matchers(names) {
    // A name two documents share can't say which it means: it matches (so it
    // isn't read as a bare page) but links nothing.
    const docIds = new Map();
    for (const { name, docId } of names) {
        const key = String(name ?? '').trim().toLowerCase();
        if (!key || !docId) continue;
        docIds.set(key, docIds.has(key) && docIds.get(key) !== docId ? null : docId);
    }
    const bare = String.raw`\(${KEYWORD}${LIST}\)|\b${KEYWORD}${LIST}\b`;
    if (docIds.size === 0) return { docIds, any: new RegExp(bare, 'gi') };
    // Longest first, so "Guide.md (2)" is tried before "Guide.md".
    const alternatives = [...docIds.keys()].sort((a, b) => b.length - a.length).map(escapeRegExp).join('|');
    // A named part sits right after "(" or ";" and right before ";" or ")",
    // so "Guide.md, page 7" in running prose is not one; a joined citation is
    // several of them, and a part with another name is just left alone.
    const named = String.raw`(?<lead>[(;]\s*)(?<name>${alternatives})(?<comma>\s*,\s*)(?<keyword>${KEYWORD})(?<list>${LIST})(?=\s*[;)])`;
    return {
        docIds,
        any: new RegExp(`${named}|${bare}`, 'gi'),
        // "Per Guide.md, " right before a bare page: the reply names a document, so the page isn't the reader's.
        afterName: new RegExp(String.raw`(?:${alternatives})\s*,\s*$`, 'i'),
    };
}

const textNode = (value) => ({ type: 'text', value });
const linkNode = ({ value, page, docId }) => ({
    type: 'link',
    url: `#page-${page}`,
    children: [textNode(value)],
    // Markdown can't set attributes, so a link the model writes
    // never carries these — only citations found here do.
    data: { hProperties: { 'data-cite-page': page, ...(docId ? { 'data-cite-doc': docId } : {}) } },
});

// Nodes whose text isn't prose: code, and links the model wrote itself.
const SKIP = new Set(['link', 'linkReference', 'inlineCode', 'code', 'html', 'definition']);

/** The node(s) one hit becomes, and where the hit really ends; null if it is no citation. */
function hitNodes(value, hit, m) {
    const text = hit[0];
    if (hit.groups?.name) {
        const docId = m.docIds.get(hit.groups.name.toLowerCase());
        const pieces = docId && partPieces(hit.groups.name + hit.groups.comma + hit.groups.keyword, hit.groups.list);
        if (!pieces) return null; // a name two documents share, or no page
        const lead = hit.groups.lead;
        const at = hit.index + text.length;
        const nodes = pieces.map((p) => (p.page ? linkNode({ ...p, docId }) : textNode(p.value)));
        if (pieces.length === 1 && lead === '(' && value[at] === ')') {
            // "(Guide.md, page 7)" as a whole is the link, parentheses and all.
            return { nodes: [linkNode({ ...pieces[0], docId, value: `(${pieces[0].value})` })], end: at + 1 };
        }
        return { nodes: [textNode(lead), ...nodes], end: at };
    }
    if (m.afterName && !text.startsWith('(')) {
        const before = value.slice(Math.max(0, hit.index - LOOKBACK), hit.index);
        // "(Other.md, page 7)", a name that isn't in the scope; or "Per Guide.md, page 7", a name that is, but
        // not written as a citation. Either way the page is that document's, not the one the reply read.
        if (OPEN_NAMED.test(before) || m.afterName.test(before)) return null;
    }
    const parenthesized = text.startsWith('(');
    const bare = BARE_PART.exec(parenthesized ? text.slice(1, -1) : text);
    const pieces = bare && partPieces(bare[1], bare[2]);
    if (!pieces) return null;
    const end = hit.index + text.length;
    if (!parenthesized) return { nodes: pieces.map((p) => (p.page ? linkNode(p) : textNode(p.value))), end };
    if (pieces.length === 1) return { nodes: [linkNode({ ...pieces[0], value: text })], end }; // "(page 4)" whole
    return { nodes: [textNode('('), ...pieces.map((p) => (p.page ? linkNode(p) : textNode(p.value))), textNode(')')], end };
}

function splitText(node, m) {
    const out = [];
    let last = 0;
    for (const hit of node.value.matchAll(m.any)) {
        if (hit.index < last) continue; // inside what the previous hit took
        const found = hitNodes(node.value, hit, m);
        if (!found) continue; // not a citation: left as text
        if (hit.index > last) out.push(textNode(node.value.slice(last, hit.index)));
        out.push(...found.nodes);
        last = found.end;
    }
    if (out.length === 0) return [node];
    if (last < node.value.length) out.push(textNode(node.value.slice(last)));
    return out;
}

function walk(node, m) {
    if (!Array.isArray(node.children) || SKIP.has(node.type)) return;
    node.children = node.children.flatMap((child) => {
        if (child.type === 'text') return splitText(child, m);
        walk(child, m);
        return [child];
    });
}

/**
 * remark plugin: marks page citations as links carrying `data-cite-page`, and,
 * for "(Name, page N)" with a name from `names` ([{name, docId}]),
 * `data-cite-doc` (the document's id). A bare page carries no document.
 */
export function remarkCitations({ names = [] } = {}) {
    const m = matchers(names);
    return (tree) => walk(tree, m);
}
