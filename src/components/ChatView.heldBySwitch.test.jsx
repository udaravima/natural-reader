import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatView from './ChatView';

const theme = {
    bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '',
    text: '', textSecondary: '', textMuted: '', hover: '',
};
const A = 'ollama:gemma4:e4b';
const B = 'ollama:gemma4:31b-cloud';
const MODELS = [{ id: A, name: 'gemma4:e4b', local: true }, { id: B, name: 'gemma4:31b-cloud', local: false }];
const MESSAGES = [
    { id: 'u1', role: 'user', content: 'what is in this?', model: A, timestamp: 1,
        attachments: [{ id: 'f1', kind: 'text', name: '.env', mimeType: 'text/plain', size: 9, masked: false, real_values: true }] },
    { id: 'a1', role: 'assistant', content: 'A database password.', model: A, timestamp: 2, status: 'complete' },
];

const props = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false,
    messages: MESSAGES, isStreaming: false, selectedModel: A, reachable: true, availableModels: MODELS,
    sendMessage: vi.fn(async () => ({ sent: true })), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(),
    downloadingMessageId: null, downloadMessageAudio: vi.fn(),
    showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null,
    draft: '', setDraft: vi.fn(), pendingAttachments: [], setPendingAttachments: vi.fn(), ...over,
});

const WARNING = /\.env was sent with its real values to gemma4:e4b\. gemma4:31b-cloud won't be sent it, or the reply that read it\. Switch back to gemma4:e4b to keep them in the conversation\./;

describe('a model switch that would hide replies', () => {
    it('says nothing while the model the real values were agreed for is selected', () => {
        render(<ChatView {...props()} />);
        expect(screen.queryByText(/sent with its real values/)).toBeNull();
    });

    it('warns above the message box, before anything is sent, and names the model to go back to', () => {
        const { rerender } = render(<ChatView {...props()} />);
        rerender(<ChatView {...props({ selectedModel: B })} />);
        expect(screen.getByText(WARNING)).toBeInTheDocument();
        rerender(<ChatView {...props()} />);
        expect(screen.queryByText(/sent with its real values/)).toBeNull();
    });
});
