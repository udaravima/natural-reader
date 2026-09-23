import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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
    // The composer's in-progress state is now owned by App (lifted out of
    // ChatView so a tab switch, which unmounts ChatView, no longer loses it).
    draft: '', setDraft: vi.fn(),
    pendingAttachments: [], setPendingAttachments: vi.fn(),
    ...over,
});

describe('ChatView composer draft (lifted to App)', () => {
    it('is controlled: shows the draft prop and calls setDraft on typing', () => {
        const setDraft = vi.fn();
        render(<ChatView {...baseProps({ draft: 'hello world', setDraft })} />);
        const box = screen.getByRole('textbox');
        expect(box).toHaveValue('hello world');
        fireEvent.change(box, { target: { value: 'hello world!' } });
        expect(setDraft).toHaveBeenCalledWith('hello world!');
    });

    it('a remount still shows the retained draft (survives the tab-switch unmount)', () => {
        // App holds `draft`, so when the user leaves chat and comes back —
        // unmounting and remounting ChatView — the half-typed message is still
        // there because ChatView reads it from props, not internal state.
        const draft = 'a half-typed thought I did not send yet';
        const { unmount } = render(<ChatView {...baseProps({ draft })} />);
        expect(screen.getByRole('textbox')).toHaveValue(draft);
        unmount();
        render(<ChatView {...baseProps({ draft })} />);
        expect(screen.getByRole('textbox')).toHaveValue(draft);
    });
});
