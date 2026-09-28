import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatView from './ChatView';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, messages: [], isStreaming: false,
    selectedModel: 'ollama:m', reachable: true, sendMessage: vi.fn(), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(), downloadingMessageId: null,
    downloadMessageAudio: vi.fn(), showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null, ...over,
});
const q = { id: 'u1', role: 'user', content: 'q' };

describe('ChatView — reply status and context notes', () => {
    it('shows "Still generating…" for a reply the server is still writing after a reload', () => {
        render(<ChatView {...baseProps({ messages: [q, { id: 'a1', role: 'assistant', content: 'Par', status: 'streaming' }] })} />);
        expect(screen.getByText(/still generating/i)).toBeInTheDocument();
    });

    it('marks stopped and failed replies', () => {
        render(<ChatView {...baseProps({ messages: [q,
            { id: 'a1', role: 'assistant', content: 'half', status: 'aborted' },
            { id: 'u2', role: 'user', content: 'again' },
            { id: 'a2', role: 'assistant', content: 'x', status: 'error', error: { message: "Can't reach the model provider." } }] })} />);
        expect(screen.getByText('Stopped')).toBeInTheDocument();
        expect(screen.getByText("Can't reach the model provider.")).toBeInTheDocument();
    });

    it('explains a turn that ran out of tool rounds (finishReason max-steps)', () => {
        render(<ChatView {...baseProps({ messages: [q, { id: 'a1', role: 'assistant', content: '', status: 'complete', finishReason: 'max-steps' }] })} />);
        expect(screen.getByText(/used its tool rounds without writing an answer/i)).toBeInTheDocument();
    });

    it('shows the stage-0 notes on the reply', () => {
        const notes = [{ kind: 'prefetch', docName: 'Thesis.pdf', count: 4 }, { kind: 'trimmed', messages: 6, attachments: 1 }];
        render(<ChatView {...baseProps({ messages: [q, { id: 'a1', role: 'assistant', content: 'ok', status: 'complete', docContext: { notes } }] })} />);
        expect(screen.getByText('Used 4 passages from Thesis.pdf · Trimmed 6 older messages and 1 older attachment to fit')).toBeInTheDocument();
    });

    it('no longer mentions Ollama host/port when the server is unreachable', () => {
        render(<ChatView {...baseProps({ reachable: false })} />);
        expect(screen.queryByText(/host & port/i)).toBeNull();
        expect(screen.getByText(/can't reach the model server/i)).toBeInTheDocument();
    });
});
