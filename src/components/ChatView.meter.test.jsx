import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatView from './ChatView';

const theme = {
    bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '',
    text: '', textSecondary: '', textMuted: '', hover: '',
};

const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false,
    messages: [], isStreaming: false, selectedModel: 'qwen3.5:latest', reachable: true,
    sendMessage: vi.fn(), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(),
    downloadingMessageId: null, downloadMessageAudio: vi.fn(),
    showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null,
    ...over,
});

describe('ChatView context meter', () => {
    it('is hidden when there is nothing to count', () => {
        render(<ChatView {...baseProps()} />);
        expect(screen.queryByTestId('context-meter')).not.toBeInTheDocument();
    });

    it('shows an estimate with no denominator when num_ctx is unset', () => {
        const messages = [{ role: 'user', content: 'a'.repeat(4000), id: 'u1' }];
        render(<ChatView {...baseProps({ messages })} />);
        expect(screen.getByTestId('context-meter')).toHaveTextContent('~1.0k ctx');
    });

    it('shows a denominator when num_ctx is set', () => {
        const messages = [{ role: 'user', content: 'a'.repeat(4000), id: 'u1' }];
        render(<ChatView {...baseProps({ messages, numCtx: 16384 })} />);
        expect(screen.getByTestId('context-meter')).toHaveTextContent('~1.0k / 16k ctx');
    });

    it('counts pin text as well as messages', () => {
        // The meter counts buildPinPreamble's wrapped output, not raw p.text — the
        // preamble wraps the 4000-char excerpt in ~217 chars of framing (header,
        // kind/page line, """ fences, trailing instruction), giving 4217 chars ->
        // ceil(4217/4) = 1055 tokens -> "1.1k" (recomputed from chatHistory.js's
        // buildPinPreamble, not guessed).
        const pins = [{ id: 'p1', doc_id: 'd', kind: 'page', text: 'a'.repeat(4000), fileName: 'f', page: 1 }];
        render(<ChatView {...baseProps({ pins, numCtx: 16384 })} />);
        expect(screen.getByTestId('context-meter')).toHaveTextContent('~1.1k / 16k ctx');
    });

    it('counts messages and pins together in a single reading', () => {
        // 4000-char message + the same 4000-char-excerpt pin as above (4217-char
        // wrapped preamble) = 8217 chars -> ceil(8217/4) = 2055 tokens -> "2.1k".
        // Neither alone would produce this figure (message alone -> 1.0k, pin
        // alone -> 1.1k), so this confirms the two sources are summed, not just
        // each read in isolation.
        const messages = [{ role: 'user', content: 'a'.repeat(4000), id: 'u1' }];
        const pins = [{ id: 'p1', doc_id: 'd', kind: 'page', text: 'a'.repeat(4000), fileName: 'f', page: 1 }];
        render(<ChatView {...baseProps({ messages, pins, numCtx: 16384 })} />);
        expect(screen.getByTestId('context-meter')).toHaveTextContent('~2.1k / 16k ctx');
    });

    it('turns amber at or above 75% of the window', () => {
        const messages = [{ role: 'user', content: 'a'.repeat(13000), id: 'u1' }];
        render(<ChatView {...baseProps({ messages, numCtx: 4096 })} />);
        expect(screen.getByTestId('context-meter').className).toMatch(/amber/);
    });

    it('stays plain just below the 75% amber boundary', () => {
        // 11996 chars -> ceil(11996/4) = 2999 tokens against a 4000 window ->
        // ratio 0.74975, just under the 0.75 cutoff.
        const messages = [{ role: 'user', content: 'a'.repeat(11996), id: 'u1' }];
        render(<ChatView {...baseProps({ messages, numCtx: 4000 })} />);
        expect(screen.getByTestId('context-meter').className).not.toMatch(/amber/);
    });

    it('turns amber exactly at the 75% boundary', () => {
        // 12000 chars -> ceil(12000/4) = 3000 tokens against a 4000 window ->
        // ratio exactly 0.75, the ">=" edge.
        const messages = [{ role: 'user', content: 'a'.repeat(12000), id: 'u1' }];
        render(<ChatView {...baseProps({ messages, numCtx: 4000 })} />);
        expect(screen.getByTestId('context-meter').className).toMatch(/amber/);
    });
});
