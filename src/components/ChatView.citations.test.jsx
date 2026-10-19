import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatView from './ChatView';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, messages: [], isStreaming: false,
    selectedModel: 'ollama:m', reachable: true, sendMessage: vi.fn(), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(), downloadingMessageId: null,
    downloadMessageAudio: vi.fn(), showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null, ...over,
});
const DOC = 'a'.repeat(64);
const q = { id: 'u1', role: 'user', content: 'q' };
const reply = (content, extra = {}) => ({ id: 'a1', role: 'assistant', content, status: 'complete', ...extra });
const prefetch = { docContext: { notes: [{ kind: 'prefetch', docId: DOC, docName: 'Thesis.pdf', count: 2, pages: [4] }] } };
const searched = {
    toolCalls: [{ name: 'search_document', arguments: { query: 'x' },
        result_summary: { ok: true, chunk_count: 1, query: 'x', docId: DOC, docName: 'Thesis.pdf' } }],
};

const renderReply = (message, onOpenCitation = vi.fn()) => {
    render(<ChatView {...baseProps({ messages: [q, message], onOpenCitation })} />);
    return onOpenCitation;
};

describe('ChatView — page citations', () => {
    it('links "(page 4)" and "page 7" when the reply used a document (prefetch note)', () => {
        const onOpen = renderReply(reply('The method is on (page 4), results on page 7.', prefetch));
        fireEvent.click(screen.getByRole('button', { name: 'Open Thesis.pdf at page 4' }));
        expect(onOpen).toHaveBeenCalledWith(DOC, 4, 'Thesis.pdf');
        fireEvent.click(screen.getByRole('button', { name: 'Open Thesis.pdf at page 7' }));
        expect(onOpen).toHaveBeenLastCalledWith(DOC, 7, 'Thesis.pdf');
        // The reply still reads the same.
        expect(screen.getByText(/The method is on/).textContent)
            .toBe('The method is on (page 4), results on page 7.');
    });

    it('links citations when the document came from a search_document call', () => {
        const onOpen = renderReply(reply('[2] (Page 3) says so.', searched));
        fireEvent.click(screen.getByRole('button', { name: 'Open Thesis.pdf at page 3' }));
        expect(onOpen).toHaveBeenCalledWith(DOC, 3, 'Thesis.pdf');
    });

    it('links citations inside list items and bold text too', () => {
        renderReply(reply('- first: **see page 2**\n- second (page 5)', prefetch));
        expect(screen.getByRole('button', { name: 'Open Thesis.pdf at page 2' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Open Thesis.pdf at page 5' })).toBeInTheDocument();
    });

    it('does not link a reply with no document context', () => {
        renderReply(reply('See (page 4).'));
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
        expect(screen.getByText('See (page 4).')).toBeInTheDocument();
    });

    it('does not link a search_document call that failed (no docId)', () => {
        renderReply(reply('See (page 4).', { toolCalls: [{ name: 'search_document', arguments: {}, result_summary: { error: 'x' } }] }));
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
    });

    it('does not link inside code, or a markdown link the model wrote', () => {
        renderReply(reply('Run `page 3` or read [page 9](https://example.com/p).', prefetch));
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
        expect(screen.getByRole('link', { name: 'page 9' })).toHaveAttribute('href', 'https://example.com/p');
    });

    it('does not link user messages', () => {
        render(<ChatView {...baseProps({ messages: [{ ...q, content: 'what is on page 4?' }], onOpenCitation: vi.fn() })} />);
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
    });

    it('does not link without an onOpenCitation handler', () => {
        render(<ChatView {...baseProps({ messages: [q, reply('See (page 4).', prefetch)] })} />);
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
    });

    it('never links page 0, or a hand-written #page link', () => {
        renderReply(reply('Not page 0, nor [page 4](#page-4).', prefetch));
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
        expect(screen.queryByRole('link', { name: 'page 0' })).toBeNull(); // plain text
    });

    it('keeps the rendered reply (no remount) when the handler is a new function each render', () => {
        const message = reply('The method is on (page 4).', prefetch);
        const { rerender } = render(<ChatView {...baseProps({ messages: [q, message], onOpenCitation: vi.fn() })} />);
        const before = screen.getByRole('button', { name: 'Open Thesis.pdf at page 4' });
        const onOpen = vi.fn();
        rerender(<ChatView {...baseProps({ messages: [q, message], onOpenCitation: onOpen })} />);
        const after = screen.getByRole('button', { name: 'Open Thesis.pdf at page 4' });
        expect(after).toBe(before);
        fireEvent.click(after);
        expect(onOpen).toHaveBeenCalledWith(DOC, 4, 'Thesis.pdf'); // the latest handler
    });
});

describe('ChatView — tool call disclosure', () => {
    const call = (summary) => reply('Answer.', { toolCalls: [{ name: 'search_documents', arguments: { query: 'x' }, result_summary: summary }] });

    it('counts the new passages a search_documents call returned', () => {
        renderReply(call({ ok: true, chunk_count: 2, docId: DOC, passages: [{ page: 1, score: 0.8 }, { page: 2, score: 0.6 }] }));
        fireEvent.click(screen.getByTitle('Show tool calls'));
        expect(screen.getByText(/2 new passages/)).toBeTruthy();
    });

    it('says "nothing new" when every passage found was already shown', () => {
        renderReply(call({ ok: true, chunk_count: 0, docId: DOC, passages: [] }));
        fireEvent.click(screen.getByTitle('Show tool calls'));
        expect(screen.getByText(/nothing new/)).toBeTruthy();
    });
});

describe('ChatView — source chips (v2.4 Task D)', () => {
    const sources = (used) => ({ docContext: { notes: [
        { kind: 'sources', docId: DOC, docName: 'Thesis.pdf', pages: [3, 8], used }] } });

    it('shows the pages an answer used as chips that open them', () => {
        const onOpen = renderReply(reply('It uses ZEPHYR-9, collected by Aldermoor.', sources(true)));
        expect(screen.getByText('Sources')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Source: page 8 of Thesis.pdf' }));
        expect(onOpen).toHaveBeenCalledWith(DOC, 8, 'Thesis.pdf');
    });

    it('labels pages only searched as not cited', () => {
        renderReply(reply('The document does not seem to cover it.', sources(false)));
        expect(screen.getByText('Searched (not cited)')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Source: page 3 of Thesis.pdf' })).toBeInTheDocument();
    });

    it('shows no chips without an open handler', () => {
        render(<ChatView {...baseProps({ messages: [q, reply('x', sources(true))] })} />);
        expect(screen.queryByText('Sources')).toBeNull();
    });
});

// C2.1 Task 16: a project reply cites several documents by name.
describe('ChatView — project citations (C2.1)', () => {
    const B = 'b'.repeat(64);
    const scope = (...docs) => ({ kind: 'scope', documents: docs.map(([name, docId], i) => ({ ref: i + 1, docId, name })) });
    const project = (...notes) => ({ docContext: { notes: [scope(['Guide.md', DOC], ['Notes.txt', B]), ...notes] } });

    it('links a named citation to its document', () => {
        const onOpen = renderReply(reply('It is set up in (Guide.md, page 7).', project()));
        fireEvent.click(screen.getByRole('button', { name: 'Open Guide.md at page 7' }));
        expect(onOpen).toHaveBeenCalledWith(DOC, 7, 'Guide.md');
        expect(screen.getByText(/It is set up in/).textContent).toBe('It is set up in (Guide.md, page 7).');
    });

    it('links each part of a joined citation to its own document', () => {
        const onOpen = renderReply(reply('Both agree (Guide.md, page 3; Notes.txt, pages 1-2 and 5).', project()));
        fireEvent.click(screen.getByRole('button', { name: 'Open Guide.md at page 3' }));
        expect(onOpen).toHaveBeenLastCalledWith(DOC, 3, 'Guide.md');
        fireEvent.click(screen.getByRole('button', { name: 'Open Notes.txt at page 1' }));
        expect(onOpen).toHaveBeenLastCalledWith(B, 1, 'Notes.txt');
        fireEvent.click(screen.getByRole('button', { name: 'Open Notes.txt at page 5' }));
        expect(onOpen).toHaveBeenLastCalledWith(B, 5, 'Notes.txt');
        expect(screen.getByText(/Both agree/).textContent).toBe('Both agree (Guide.md, page 3; Notes.txt, pages 1-2 and 5).');
    });

    it('leaves a name that is not in the scope as text', () => {
        renderReply(reply('See (Elsewhere.md, page 7).', project()));
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
        expect(screen.getByText('See (Elsewhere.md, page 7).')).toBeInTheDocument();
    });

    it('a bare page links only when the reply has one document', () => {
        // Evidence from two documents: "(page 4)" says nothing about which.
        renderReply(reply('Also (page 4).', project(
            { kind: 'sources', documents: [{ docId: DOC, docName: 'Guide.md', pages: [1], used: true }, { docId: B, docName: 'Notes.txt', pages: [2], used: true }] })));
        expect(screen.queryByRole('button', { name: /Open .* at page 4/ })).toBeNull();
        expect(screen.getByText('Also (page 4).')).toBeInTheDocument();
    });

    it('a bare page links to the one document a project reply read', () => {
        const onOpen = renderReply(reply('Also (page 4).', project(
            { kind: 'prefetch', documents: [{ docId: B, docName: 'Notes.txt', count: 1, pages: [4] }], count: 1 })));
        fireEvent.click(screen.getByRole('button', { name: 'Open Notes.txt at page 4' }));
        expect(onOpen).toHaveBeenCalledWith(B, 4, 'Notes.txt');
    });

    it('a citation in an earlier reply opens that reply\'s document after the scope changed', () => {
        // The same name, two different documents: each reply carries its own scope.
        const first = reply('First (Guide.md, page 2).', { id: 'a1', docContext: { notes: [scope(['Guide.md', DOC])] } });
        const second = reply('Then (Guide.md, page 9).', { id: 'a2', docContext: { notes: [scope(['Guide.md', B])] } });
        const onOpen = vi.fn();
        render(<ChatView {...baseProps({ messages: [q, first, { ...q, id: 'u2' }, second], onOpenCitation: onOpen })} />);
        fireEvent.click(screen.getByRole('button', { name: 'Open Guide.md at page 2' }));
        expect(onOpen).toHaveBeenLastCalledWith(DOC, 2, 'Guide.md');
        fireEvent.click(screen.getByRole('button', { name: 'Open Guide.md at page 9' }));
        expect(onOpen).toHaveBeenLastCalledWith(B, 9, 'Guide.md');
    });

    it('does not open the one document it read for a different name in running prose', () => {
        // The evidence is Notes.txt only; the text says Guide.md. A bare "page 7" here would open Notes.txt.
        renderReply(reply('Per Guide.md, page 7 has it.', project(
            { kind: 'sources', documents: [{ docId: B, docName: 'Notes.txt', pages: [7], used: true }] })));
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
        expect(screen.getByText('Per Guide.md, page 7 has it.')).toBeInTheDocument();
    });

    it('does not link names when the reply has no scope note', () => {
        renderReply(reply('See (Guide.md, page 7).'));
        expect(screen.queryByRole('button', { name: /at page/ })).toBeNull();
    });

    it('keeps the rendered reply (no remount) when only the identity of the notes changes', () => {
        const withNotes = () => reply('See (Guide.md, page 7).', project());
        const { rerender } = render(<ChatView {...baseProps({ messages: [q, withNotes()], onOpenCitation: vi.fn() })} />);
        const before = screen.getByRole('button', { name: 'Open Guide.md at page 7' });
        rerender(<ChatView {...baseProps({ messages: [q, withNotes()], onOpenCitation: vi.fn() })} />);
        expect(screen.getByRole('button', { name: 'Open Guide.md at page 7' })).toBe(before);
    });
});

describe('ChatView — project source chips (C2.1)', () => {
    const B = 'b'.repeat(64);
    const docs = (used) => ({ docContext: { notes: [{ kind: 'sources', documents: [
        { docId: DOC, docName: 'README.md', pages: [1, 3], used },
        { docId: B, docName: 'Notes.txt', pages: [2], used }] }] } });

    it('project source chips group by document', () => {
        const onOpen = renderReply(reply('Answer.', docs(true)));
        expect(screen.getByText('Sources')).toBeInTheDocument();
        expect(screen.getByText('README.md').parentElement.textContent).toBe('README.md · p. 1 · p. 3');
        expect(screen.getByText('Notes.txt').parentElement.textContent).toBe('Notes.txt · p. 2');
        fireEvent.click(screen.getByRole('button', { name: 'Source: page 3 of README.md' }));
        expect(onOpen).toHaveBeenCalledWith(DOC, 3, 'README.md');
        fireEvent.click(screen.getByRole('button', { name: 'Source: page 2 of Notes.txt' }));
        expect(onOpen).toHaveBeenLastCalledWith(B, 2, 'Notes.txt');
    });

    it('labels documents that were only searched as not cited', () => {
        renderReply(reply('Answer.', { docContext: { notes: [{ kind: 'sources', documents: [
            { docId: DOC, docName: 'README.md', pages: [1], used: true },
            { docId: B, docName: 'Notes.txt', pages: [2], used: false }] }] } }));
        expect(screen.getByText('Sources')).toBeInTheDocument();
        expect(screen.getByText('Searched (not cited)')).toBeInTheDocument();
    });

    it('skips a document with no pages, and shows nothing when none has any', () => {
        renderReply(reply('Answer.', { docContext: { notes: [{ kind: 'sources', documents: [{ docId: DOC, docName: 'README.md', pages: [], used: true }] }] } }));
        expect(screen.queryByText('Sources')).toBeNull();
    });

    it('shows no document chips without an open handler', () => {
        render(<ChatView {...baseProps({ messages: [q, reply('x', docs(true))] })} />);
        expect(screen.queryByText('Sources')).toBeNull();
    });
});

describe('ChatView — web sources (C2.1)', () => {
    const web = (...pages) => ({ docContext: { notes: [{ kind: 'web_sources', pages }] } });

    it('web sources render as external links', () => {
        renderReply(reply('Answer.', web(
            { n: 1, title: 'Zephyr 9 release notes', url: 'https://www.example.com/zephyr?x=1' },
            { n: 2, title: 'Other', url: 'http://docs.example.org/a' })));
        const link = screen.getByRole('link', { name: 'example.com' });
        expect(link).toHaveAttribute('href', 'https://www.example.com/zephyr?x=1');
        expect(link).toHaveAttribute('target', '_blank');
        expect(link).toHaveAttribute('rel', 'noopener noreferrer');
        expect(link).toHaveAttribute('title', 'Zephyr 9 release notes');
        expect(screen.getByRole('link', { name: 'docs.example.org' })).toHaveAttribute('href', 'http://docs.example.org/a');
    });

    it('shows web links without an open-citation handler', () => {
        render(<ChatView {...baseProps({ messages: [q, reply('x', web({ n: 1, title: 'T', url: 'https://example.com/' }))] })} />);
        expect(screen.getByRole('link', { name: 'example.com' })).toBeInTheDocument();
    });

    it('renders a url that is not http(s) as text, never as a link', () => {
        renderReply(reply('Answer.', web(
            { n: 1, title: 'Click me', url: 'javascript:alert(1)' },
            { n: 2, title: 'Data', url: 'data:text/html,<b>x</b>' },
            { n: 3, title: 'Not a url', url: 'nonsense' },
            { n: 4, title: 'Fine', url: 'https://example.com/' })));
        expect(screen.getAllByRole('link')).toHaveLength(1);
        expect(screen.getByText('Click me')).toBeInTheDocument();
        expect(screen.getByText('Click me').closest('a')).toBeNull();
        expect(screen.getByText('Not a url')).toBeInTheDocument();
    });

    it.each([
        ['JAVASCRIPT:alert(1)'],
        ['  javascript:alert(1)'],
        ['java\tscript:alert(1)'],
        ['java\nscript:alert(1)'],
        ['//evil.com/x'],
        ['/relative/path'],
        ['vbscript:msgbox(1)'],
        ['file:///etc/passwd'],
        [''],
    ])('renders the url %j as text, never as a link', (url) => {
        renderReply(reply('Answer.', web({ n: 1, title: 'Trap', url })));
        expect(screen.queryByRole('link')).toBeNull();
        expect(screen.getByText('Trap').closest('a')).toBeNull();
    });

    it('accepts HTTPS and HTTP in any case', () => {
        renderReply(reply('Answer.', web({ n: 1, title: 'T', url: 'HTTPS://Example.COM/a' })));
        expect(screen.getByRole('link', { name: 'example.com' })).toHaveAttribute('href', 'https://example.com/a');
    });

    it('shows a title as literal text, never parsed as markup', () => {
        const markup = '<img src=x onerror=alert(1)>';
        const { container } = render(<ChatView {...baseProps({ messages: [q, reply('x', web(
            { n: 1, title: markup, url: 'https://example.com/' },
            { n: 2, title: markup, url: 'javascript:alert(1)' }))], onOpenCitation: vi.fn() })} />);
        expect(container.querySelector('img')).toBeNull();
        // A link's title arrives unparsed in its attribute; a link-less one shows it as text.
        expect(screen.getByRole('link', { name: 'example.com' })).toHaveAttribute('title', markup);
        expect(screen.getByText(markup)).toBeInTheDocument();
        expect(container.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;');
    });

    it('tells apart pages of one site by their result number, and keeps a lone site plain', () => {
        renderReply(reply('Answer.', web(
            { n: 1, title: 'Install', url: 'https://docs.example.org/install' },
            { n: 2, title: 'Configure <b>it</b>', url: 'https://docs.example.org/config' },
            { n: 4, title: 'Blog', url: 'https://www.docs.example.org/blog' },
            { n: 3, title: 'Elsewhere', url: 'https://other.example.com/x' })));
        const one = screen.getByRole('link', { name: 'docs.example.org · 1: Install' });
        expect(one).toHaveTextContent('docs.example.org · 1');
        expect(one).toHaveAttribute('href', 'https://docs.example.org/install');
        const two = screen.getByRole('link', { name: 'docs.example.org · 2: Configure <b>it</b>' });
        expect(two).toHaveTextContent('docs.example.org · 2');
        expect(two).toHaveAttribute('title', 'Configure <b>it</b>');
        expect(screen.getByRole('link', { name: 'docs.example.org · 4: Blog' })).toHaveTextContent('docs.example.org · 4'); // "www." is dropped before comparing
        const lone = screen.getByRole('link', { name: 'other.example.com' });
        expect(lone).toHaveTextContent(/^other\.example\.com$/);
    });

    it('does not number two pages that share no host', () => {
        renderReply(reply('Answer.', web(
            { n: 1, title: 'A', url: 'https://a.example.com/' }, { n: 2, title: 'B', url: 'https://b.example.com/' })));
        expect(screen.getByRole('link', { name: 'a.example.com' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'b.example.com' })).toBeInTheDocument();
    });

    it('shows document sources and web sources together', () => {
        renderReply(reply('Answer.', { docContext: { notes: [
            { kind: 'sources', docId: DOC, docName: 'Thesis.pdf', pages: [3], used: true },
            { kind: 'web_sources', pages: [{ n: 1, title: 'T', url: 'https://example.com/' }] }] } }));
        expect(screen.getByRole('button', { name: 'Source: page 3 of Thesis.pdf' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'example.com' })).toBeInTheDocument();
    });

    it('keeps a reply with an offer note normal: the text, no error, the card beside it (Task 18)', () => {
        renderReply(reply('Not in the files.', { docContext: { notes: [{ kind: 'web_offer', query: 'zephyr release', reason: 'files are silent' }] } }));
        expect(screen.getByText('Not in the files.')).toBeInTheDocument();
        expect(screen.getByRole('region', { name: 'Web search offer' })).toHaveTextContent('files are silent');
        expect(screen.queryByText(/failed|error/i)).toBeNull();
    });
});

describe('ChatView — project prefetch note (C2.1)', () => {
    const notesReply = (notes) => reply('ok', { docContext: { notes } });

    it('says how many passages came from how many documents', () => {
        renderReply(notesReply([{ kind: 'prefetch', documents: [{ docId: DOC, docName: 'A.md', count: 2, pages: [1] }, { docId: 'b', docName: 'B.md', count: 2 }, { docId: 'c', docName: 'C.md', count: 1 }], count: 5, topScore: 0.8 }]));
        expect(screen.getByText('Used 5 passages from 3 documents')).toBeInTheDocument();
        expect(screen.queryByText(/undefined/)).toBeNull();
    });

    it('uses the singular for one passage from one document', () => {
        renderReply(notesReply([{ kind: 'prefetch', documents: [{ docId: DOC, docName: 'A.md', count: 1, pages: [1] }], count: 1 }]));
        expect(screen.getByText('Used 1 passage from 1 document')).toBeInTheDocument();
    });

    it('still names the document for a document-scope prefetch', () => {
        renderReply(notesReply([{ kind: 'prefetch', docId: DOC, docName: 'Thesis.pdf', count: 4 }]));
        expect(screen.getByText('Used 4 passages from Thesis.pdf')).toBeInTheDocument();
    });

    it('prints nothing for the scope note', () => {
        renderReply(notesReply([{ kind: 'scope', documents: [{ ref: 1, docId: DOC, name: 'A.md' }] }]));
        expect(screen.queryByText(/A\.md/)).toBeNull();
    });
});
