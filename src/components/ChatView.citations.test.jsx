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
});
