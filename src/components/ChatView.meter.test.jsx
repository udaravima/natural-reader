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
        const pins = [{ id: 'p1', doc_id: 'd', kind: 'page', text: 'a'.repeat(4000), fileName: 'f', page: 1 }];
        render(<ChatView {...baseProps({ pins, numCtx: 16384 })} />);
        expect(screen.getByTestId('context-meter')).toHaveTextContent('~1.0k / 16k ctx');
    });

    it('turns amber at or above 75% of the window', () => {
        const messages = [{ role: 'user', content: 'a'.repeat(13000), id: 'u1' }];
        render(<ChatView {...baseProps({ messages, numCtx: 4096 })} />);
        expect(screen.getByTestId('context-meter').className).toMatch(/amber/);
    });
});
