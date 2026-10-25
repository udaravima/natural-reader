import { describe, it, expect } from 'vitest';
import { citationDoc, citationDocs, remarkCitations } from './citations';

const DOC = 'a'.repeat(64);

describe('citationDoc', () => {
    it('takes the document from the prefetch note', () => {
        expect(citationDoc({ docContext: { notes: [{ kind: 'trimmed' }, { kind: 'prefetch', docId: DOC, docName: 'T.pdf' }] } }))
            .toEqual({ docId: DOC, docName: 'T.pdf' });
    });

    it('else from a successful search_document call', () => {
        expect(citationDoc({ toolCalls: [
            { name: 'web_search', result_summary: { ok: true } },
            { name: 'search_document', result_summary: { error: 'x' } },
            { name: 'search_document', result_summary: { ok: true, docId: DOC, docName: 'T.pdf' } },
        ] })).toEqual({ docId: DOC, docName: 'T.pdf' });
    });

    it('from any document tool: search_documents, and search_document in chats saved before v2.3', () => {
        for (const name of ['search_documents', 'search_document', 'read_document_pages']) {
            expect(citationDoc({ toolCalls: [
                { name, result_summary: { ok: true, docId: DOC, docName: 'T.pdf' } },
            ] })).toEqual({ docId: DOC, docName: 'T.pdf' });
        }
    });

    it('is null for a reply that used no document', () => {
        expect(citationDoc({})).toBeNull();
        expect(citationDoc({ docContext: { notes: [{ kind: 'prefetch' }] }, toolCalls: [{ name: 'web_search' }] })).toBeNull();
        expect(citationDoc(null)).toBeNull();
    });
});

describe('citationDoc and source chips (v2.4 Task D)', () => {
    it('finds the document from a sources note alone', () => {
        const message = { docContext: { notes: [{ kind: 'sources', docId: 'd', docName: 'T.pdf', pages: [2], used: true }] } };
        expect(citationDoc(message)).toEqual({ docId: 'd', docName: 'T.pdf' });
    });
});

const A = 'a'.repeat(64);
const B = 'b'.repeat(64);
const scope = (...documents) => ({ kind: 'scope', documents: documents.map(([name, docId], i) => ({ ref: i + 1, docId, name })) });

describe('citationDocs', () => {
    it('takes the names from the reply\'s own scope note', () => {
        const message = { docContext: { notes: [{ kind: 'trimmed' }, scope(['Guide.md', A], ['Notes.txt', B])] } };
        expect(citationDocs(message).names).toEqual([{ name: 'Guide.md', docId: A }, { name: 'Notes.txt', docId: B }]);
    });

    it('has no names for a document-scope reply, and keeps today\'s single document', () => {
        const message = { docContext: { notes: [{ kind: 'prefetch', docId: A, docName: 'T.pdf' }] } };
        expect(citationDocs(message)).toEqual({ names: [], single: { docId: A, docName: 'T.pdf' } });
        expect(citationDocs(null)).toEqual({ names: [], single: null });
    });

    it('ignores scope entries without a name or an id', () => {
        const message = { docContext: { notes: [{ kind: 'scope', documents: [{ docId: A }, { name: 'X.md' }, null, { docId: B, name: 'Y.md' }] }] } };
        expect(citationDocs(message).names).toEqual([{ name: 'Y.md', docId: B }]);
    });

    it('has no single document for a project reply that read several', () => {
        const message = { docContext: { notes: [scope(['Guide.md', A], ['Notes.txt', B]),
            { kind: 'prefetch', documents: [{ docId: A, docName: 'Guide.md', count: 2, pages: [1] }], count: 2 },
            { kind: 'sources', documents: [{ docId: B, docName: 'Notes.txt', pages: [2], used: true }] }] } };
        expect(citationDocs(message).single).toBeNull();
    });

    it('has a single document when a project reply\'s evidence is one document', () => {
        const prefetchOnly = { docContext: { notes: [scope(['Guide.md', A], ['Notes.txt', B]),
            { kind: 'prefetch', documents: [{ docId: A, docName: 'Guide.md', count: 2, pages: [1] }], count: 2 }] } };
        expect(citationDocs(prefetchOnly).single).toEqual({ docId: A, docName: 'Guide.md' });
        const sourcesOnly = { docContext: { notes: [scope(['Guide.md', A], ['Notes.txt', B]),
            { kind: 'sources', documents: [{ docId: B, docName: 'Notes.txt', pages: [2], used: true }] }] } };
        expect(citationDocs(sourcesOnly).single).toEqual({ docId: B, docName: 'Notes.txt' });
        const toolOnly = { docContext: { notes: [scope(['Guide.md', A])] }, toolCalls: [
            { name: 'extract_from_documents', result_summary: { ok: true, documents: [{ docId: A, docName: 'Guide.md', pages: [3] }] } },
            { name: 'document_outline', result_summary: { ok: true, document: { docId: A, docName: 'Guide.md' } } },
            { name: 'search_documents', result_summary: { ok: true, passages: [{ docId: A, page: 2 }] } }] };
        expect(citationDocs(toolOnly).single).toEqual({ docId: A, docName: 'Guide.md' });
    });

    it('takes a document\'s name from the scope when the evidence has none (search passages)', () => {
        const message = { docContext: { notes: [scope(['Guide.md', A], ['Notes.txt', B])] },
            toolCalls: [{ name: 'search_documents', result_summary: { ok: true, passages: [{ docId: B, page: 2 }, { docId: B, page: 5 }] } }] };
        expect(citationDocs(message).single).toEqual({ docId: B, docName: 'Notes.txt' });
    });

    it('a bare page links only when the reply has one document', () => {
        const linked = (message, text) => {
            const { names, single } = citationDocs(message);
            const tree = treeOf(text);
            remarkCitations({ names })(tree);
            // The reader links a bare page to `single`, so with no `single` it is text.
            return tree.children[0].children.filter((n) => n.type === 'link' && single).map(() => single.docId);
        };
        const two = { docContext: { notes: [scope(['Guide.md', A], ['Notes.txt', B]),
            { kind: 'sources', documents: [{ docId: A, docName: 'Guide.md', pages: [1], used: true }, { docId: B, docName: 'Notes.txt', pages: [2], used: true }] }] } };
        const one = { docContext: { notes: [scope(['Guide.md', A], ['Notes.txt', B]),
            { kind: 'sources', documents: [{ docId: B, docName: 'Notes.txt', pages: [2], used: true }] }] } };
        expect(citationDocs(two).single).toBeNull();
        expect(linked(two, 'Also (page 4).')).toEqual([]);
        expect(citationDocs(one).single).toEqual({ docId: B, docName: 'Notes.txt' });
        expect(linked(one, 'Also (page 4).')).toEqual([B]);
    });

    it('has no single document for a project reply that read nothing', () => {
        expect(citationDocs({ docContext: { notes: [scope(['Guide.md', A])] } }).single).toBeNull();
    });
});

// A paragraph of one text node, as the markdown parser hands it to the plugin.
const treeOf = (value) => ({ type: 'root', children: [{ type: 'paragraph', children: [{ type: 'text', value }] }] });
const run = (value, names = []) => {
    const tree = treeOf(value);
    remarkCitations({ names })(tree);
    return tree.children[0].children;
};
const linksIn = (nodes) => nodes.filter((n) => n.type === 'link').map((n) => ({
    text: n.children[0].value,
    page: n.data.hProperties['data-cite-page'],
    doc: n.data.hProperties['data-cite-doc'],
}));
const textOf = (nodes) => nodes.map((n) => (n.type === 'link' ? n.children[0].value : n.value)).join('');
const NAMES = [{ name: 'Guide.md', docId: A }, { name: 'Notes.txt', docId: B }];

describe('remarkCitations — named citations', () => {
    it('links a named citation to its document', () => {
        const nodes = run('See (Guide.md, page 7) for it.', NAMES);
        expect(linksIn(nodes)).toEqual([{ text: '(Guide.md, page 7)', page: 7, doc: A }]);
        expect(textOf(nodes)).toBe('See (Guide.md, page 7) for it.'); // the reply still reads the same
    });

    it('matches the name without regard to case', () => {
        expect(linksIn(run('(guide.MD, Page 2)', NAMES))).toEqual([{ text: '(guide.MD, Page 2)', page: 2, doc: A }]);
    });

    it('handles names with commas and parentheses', () => {
        const names = [{ name: 'Report, final (v2).pdf', docId: A }];
        const nodes = run('Costs are in (Report, final (v2).pdf, page 12).', names);
        expect(linksIn(nodes)).toEqual([{ text: '(Report, final (v2).pdf, page 12)', page: 12, doc: A }]);
    });

    it('prefers the longest name: "Guide.md (2)" wins over "Guide.md"', () => {
        const names = [{ name: 'Guide.md', docId: A }, { name: 'Guide.md (2)', docId: B }];
        expect(linksIn(run('(Guide.md (2), page 3)', names))).toEqual([{ text: '(Guide.md (2), page 3)', page: 3, doc: B }]);
        expect(linksIn(run('(Guide.md, page 3)', names))).toEqual([{ text: '(Guide.md, page 3)', page: 3, doc: A }]);
        // Whichever order the names arrive in.
        expect(linksIn(run('(Guide.md (2), page 3)', [...names].reverse()))[0].doc).toBe(B);
    });

    it('links the second of two long names that differ only past the cap to its own document (final review M1)', () => {
        // The server caps a name at 120 characters and numbers a repeat within the cap: "…" then " (2)".
        const head = 'Annual report of the regional archive on the migration of its collections '.repeat(2).slice(0, 120);
        const second = `${head.slice(0, 115)}… (2)`;
        const message = { docContext: { notes: [{ kind: 'scope', documents: [
            { ref: 1, docId: A, name: `${head}…` }, { ref: 2, docId: B, name: second }] }] } };
        const { names } = citationDocs(message);
        expect(linksIn(run(`See (${second}, page 2).`, names))).toEqual([{ text: `(${second}, page 2)`, page: 2, doc: B }]);
        expect(linksIn(run(`See (${head}…, page 1).`, names))[0].doc).toBe(A);
    });

    it('leaves an unknown name as text, even though "page 7" alone would link', () => {
        const nodes = run('See (Other.md, page 7) and (Guide.md (2), page 4).', NAMES);
        expect(linksIn(nodes)).toEqual([]);
        expect(nodes).toHaveLength(1);
        // The same when the name has a parenthesis of its own.
        expect(linksIn(run('(Other (2).md, page 7)', NAMES))).toEqual([]);
    });

    it('still links a bare "page N" after prose that merely has a comma', () => {
        expect(linksIn(run('As the figure shows, page 7 has it.', NAMES))).toEqual([{ text: 'page 7', page: 7, doc: undefined }]);
    });

    it('leaves "page N" as text right after a known name and a comma, in running prose', () => {
        // The reply names Guide.md; a bare page would open whichever document the reply read, maybe another one.
        expect(linksIn(run('Per Guide.md, page 7 has it.', NAMES))).toEqual([]);
        expect(linksIn(run('Per guide.MD,   pages 4 and 6 have it.', NAMES))).toEqual([]);
        expect(linksIn(run('See Notes.txt, p. 2.', NAMES))).toEqual([]);
        // Within the look-back, a name with commas and parentheses too.
        expect(linksIn(run('In Report, final (v2).pdf, page 12.', [{ name: 'Report, final (v2).pdf', docId: A }]))).toEqual([]);
        // A name that is not in the scope is not this rule's business, and a name later in the sentence is no bar.
        expect(linksIn(run('Per Other.md, page 7 has it.', NAMES))).toEqual([{ text: 'page 7', page: 7, doc: undefined }]);
        expect(linksIn(run('Guide.md is long, page 7 has it.', NAMES))).toEqual([{ text: 'page 7', page: 7, doc: undefined }]);
        // Without scope names (a document-scope reply) nothing changes.
        expect(linksIn(run('Per Guide.md, page 7 has it.'))).toEqual([{ text: 'page 7', page: 7, doc: undefined }]);
    });

    it('keeps linking "(Figure 3, page 7)" in a reply that has no scope note (one document)', () => {
        expect(linksIn(run('(Figure 3, page 7)'))).toEqual([{ text: 'page 7', page: 7, doc: undefined }]);
    });

    it('links each name-page part of a joined citation on its own', () => {
        const nodes = run('Both agree (Guide.md, page 3; Notes.txt, page 1).', NAMES);
        expect(linksIn(nodes)).toEqual([
            { text: 'Guide.md, page 3', page: 3, doc: A },
            { text: 'Notes.txt, page 1', page: 1, doc: B },
        ]);
        expect(textOf(nodes)).toBe('Both agree (Guide.md, page 3; Notes.txt, page 1).');
    });

    it('links the known parts of a joined citation and leaves an unknown name\'s part as text', () => {
        const nodes = run('(Other.md, page 9; Guide.md, page 3; Elsewhere.md (2), page 1)', NAMES);
        expect(linksIn(nodes)).toEqual([{ text: 'Guide.md, page 3', page: 3, doc: A }]);
        expect(textOf(nodes)).toBe('(Other.md, page 9; Guide.md, page 3; Elsewhere.md (2), page 1)');
    });

    it('does not take a name in running prose for a citation', () => {
        // "(Guide.md, page 7 and more)" has no ")" right after the page; "Guide.md, page 7" has no "(".
        expect(linksIn(run('Per Guide.md, page 7 has it.', NAMES))).toEqual([]);
        expect(linksIn(run('(Guide.md, page 7 and more)', NAMES))).toEqual([]);
    });

    it('does not link a name that two documents share', () => {
        const names = [{ name: 'Guide.md', docId: A }, { name: 'guide.md', docId: B }];
        expect(linksIn(run('(Guide.md, page 3)', names))).toEqual([]);
        // Two entries for the same document are no ambiguity.
        expect(linksIn(run('(Guide.md, page 3)', [{ name: 'Guide.md', docId: A }, { name: 'Guide.md', docId: A }]))).toHaveLength(1);
    });

    it('does not link inside code, or a link the model wrote', () => {
        const tree = { type: 'root', children: [{ type: 'paragraph', children: [
            { type: 'inlineCode', value: '(Guide.md, page 3)' },
            { type: 'link', url: 'https://example.com', children: [{ type: 'text', value: '(Guide.md, page 4)' }] },
        ] }] };
        remarkCitations({ names: NAMES })(tree);
        expect(tree.children[0].children[0].type).toBe('inlineCode');
        expect(tree.children[0].children[1].data).toBeUndefined();
    });

    it('does not link page 0', () => {
        expect(linksIn(run('(Guide.md, page 0)', NAMES))).toEqual([]);
    });
});

describe('remarkCitations — page forms', () => {
    it('links a range at its first page', () => {
        expect(linksIn(run('(Guide.md, pages 4-5)', NAMES))).toEqual([{ text: '(Guide.md, pages 4-5)', page: 4, doc: A }]);
        expect(linksIn(run('(Guide.md, pages 4–6)', NAMES))[0].page).toBe(4); // en dash
        expect(linksIn(run('(Guide.md, pages 4 - 6)', NAMES))[0].page).toBe(4);
    });

    it('links each page of "pages 4 and 6"', () => {
        const nodes = run('(Guide.md, pages 4 and 6)', NAMES);
        expect(linksIn(nodes)).toEqual([
            { text: 'Guide.md, pages 4', page: 4, doc: A },
            { text: '6', page: 6, doc: A },
        ]);
        expect(textOf(nodes)).toBe('(Guide.md, pages 4 and 6)');
    });

    it('takes "p." and "pp." for the page word', () => {
        expect(linksIn(run('(Guide.md, p. 7)', NAMES))).toEqual([{ text: '(Guide.md, p. 7)', page: 7, doc: A }]);
        expect(linksIn(run('(Guide.md, pp. 4-5)', NAMES))[0].page).toBe(4);
        expect(linksIn(run('(Guide.md, p.7)', NAMES))[0].page).toBe(7);
    });

    it('links a long or backwards range at its first page too', () => {
        // The server counts such a range's two ends; either way the first page is a fair place to open.
        expect(linksIn(run('(Guide.md, pages 6-4)', NAMES))).toEqual([{ text: '(Guide.md, pages 6-4)', page: 6, doc: A }]);
        expect(linksIn(run('(Guide.md, pages 1-400)', NAMES))[0].page).toBe(1);
        expect(linksIn(run('(Guide.md, pages 0-3)', NAMES))).toEqual([]); // there is no page 0
    });

    it('reads the server\'s lists: commas, "and", "&", and dashes of any length', () => {
        expect(linksIn(run('(Guide.md, pages 4, 6 & 8)', NAMES)).map((l) => l.page)).toEqual([4, 6, 8]);
        expect(linksIn(run('(Guide.md, pages 4, 5, and 6)', NAMES)).map((l) => l.page)).toEqual([4, 5, 6]);
        expect(linksIn(run('(Guide.md, pages 4—5 and 9)', NAMES)).map((l) => l.page)).toEqual([4, 9]);
        expect(textOf(run('(Guide.md, pages 4, 6 & 8)', NAMES))).toBe('(Guide.md, pages 4, 6 & 8)');
    });

    it('takes these forms without a name too, linking with no document', () => {
        expect(linksIn(run('(page 4)'))).toEqual([{ text: '(page 4)', page: 4, doc: undefined }]);
        expect(linksIn(run('on page 7.'))).toEqual([{ text: 'page 7', page: 7, doc: undefined }]);
        expect(linksIn(run('see pages 4-5'))).toEqual([{ text: 'pages 4-5', page: 4, doc: undefined }]);
        expect(linksIn(run('(pp. 4 and 6)'))).toEqual([
            { text: 'pp. 4', page: 4, doc: undefined },
            { text: '6', page: 6, doc: undefined },
        ]);
        expect(linksIn(run('see p. 9'))).toEqual([{ text: 'p. 9', page: 9, doc: undefined }]);
        expect(linksIn(run('Page 3 and PAGE 5'))).toHaveLength(2);
    });

    it('does not link a page word inside another word, or page 0', () => {
        expect(linksIn(run('the homepage 3 and app. 4 and page 0 and page 7th'))).toEqual([]);
    });

    it('a bare page links with no document (the reader decides which)', () => {
        const nodes = run('Look at (page 4).', NAMES);
        expect(linksIn(nodes)).toEqual([{ text: '(page 4)', page: 4, doc: undefined }]);
    });
});

// Server/client parity (carried from Task 15, fix round 1): the strings the server's
// test_cited_pages_counts_exactly_what_the_client_links pins in
// server/tests/test_chat_sources.py. The server counts a page for a source chip
// exactly when the client links it, so each row here is that test's row, and
// `want` is what the client links ([document, page] pairs) where the server's
// `want` is the same documents and pages. A drift on either side fails one of the two files.
describe('remarkCitations — parity with the server\'s cited_pages (Task 15)', () => {
    const G = 'g'.repeat(64);
    const T = 't'.repeat(64);
    // The server test's NAMES, in the same order.
    const SERVER_NAMES = [
        { name: 'Guide.md', docId: G }, { name: 'Guide.md (2)', docId: 'g2'.repeat(32) },
        { name: 'Notes, v2 (final).md', docId: 'n'.repeat(64) }, { name: 'Tour.md', docId: T },
    ];
    const pairs = (text) => linksIn(run(text, SERVER_NAMES)).map((l) => [l.doc, l.page]);

    it.each([
        ['(Guide.md, page 3;)', [[G, 3]]],
        ['(Guide.md, page 3; Tour.md, page 1;)', [[G, 3], [T, 1]]],
        ['(Guide.md, page 3;; Tour.md, page 1)', [[G, 3], [T, 1]]],
        ['(Guide.md, page 3; see (fig. 2))', [[G, 3]]],
        ['(Guide.md, page 3; Other (v2).md, page 2)', [[G, 3]]],
    ])('one bad item beside a good citation never costs the good one: %s', (text, want) => {
        expect(pairs(text)).toEqual(want);
    });

    it('"page3" is no page: the page word needs a space', () => {
        expect(pairs('(Guide.md, page3)')).toEqual([]);
        expect(pairs('(Guide.md, pages3)')).toEqual([]);
        expect(linksIn(run('Look at (page3).'))).toEqual([]);
    });

    it('a list holding page 0 is no citation at all', () => {
        expect(pairs('(Guide.md, pages 0-3)')).toEqual([]);
        expect(pairs('(Guide.md, pages 0 and 4)')).toEqual([]);
        expect(pairs('(Guide.md, pages 4 and 0)')).toEqual([]);
        expect(linksIn(run('(pages 0-3)'))).toEqual([]);
        expect(linksIn(run('Look at (pages 0 and 4).'))).toEqual([]);
    });
});
