import { describe, it, expect } from 'vitest';
import { citationDoc } from './citations';

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
