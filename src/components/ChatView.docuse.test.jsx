import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatView from './ChatView';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const props = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, messages: [], isStreaming: false,
    selectedModel: 'ollama:m', reachable: true, sendMessage: vi.fn(), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(), downloadingMessageId: null,
    downloadMessageAudio: vi.fn(), showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null, ...over,
});

describe('ChatView — "Use this document" (v2.4 Task E)', () => {
    it('shows the switch only with a document open', () => {
        render(<ChatView {...props()} />);
        expect(screen.queryByRole('switch', { name: /Use this document/ })).toBeNull();
    });

    it('shows the open document and toggles the switch', () => {
        const setEnabled = vi.fn();
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', docUse: { enabled: true, setEnabled } })} />);
        const sw = screen.getByRole('switch', { name: 'Use this document: Thesis.pdf' });
        expect(sw.getAttribute('aria-checked')).toBe('true');
        fireEvent.click(sw);
        expect(setEnabled).toHaveBeenCalledWith(false);
    });

    it('says the assistant won\'t read it when off', () => {
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', docUse: { enabled: false, setEnabled: vi.fn() } })} />);
        const sw = screen.getByRole('switch', { name: 'Use this document: Thesis.pdf' });
        expect(sw.getAttribute('aria-checked')).toBe('false');
        expect(sw.getAttribute('title')).toBe("The assistant won't read this document in this chat.");
    });
});
