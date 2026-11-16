import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import ChatView from './ChatView';

// Choosing who answers where the person chats (character profiles spec, "Choosing in the chat").
const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '',
    textMuted: '', hover: '' };
const STORY = { id: 'p1', name: 'The Story Teller', description: 'A wise old man', isDefault: true };
const LIB = { id: 'p2', name: 'The Librarian', description: 'Careful answers', isDefault: false, builtin: true };
const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, messages: [], isStreaming: false, selectedModel: 'ollama:m',
    reachable: true, sendMessage: vi.fn(), stopStream: vi.fn(), speakingMessageId: null, speakMessage: vi.fn(),
    stopSpeaking: vi.fn(), downloadingMessageId: null, downloadMessageAudio: vi.fn(), showToast: vi.fn(), pins: [],
    onRemovePin: vi.fn(), numCtx: null,
    profiles: [STORY, LIB], profileId: 'p1', setProfile: vi.fn(), refreshProfiles: vi.fn(), currentProfile: null,
    removedProfile: null, ...over,
});

describe('ChatView — who answers', () => {
    it('names the profile beside the message box', () => {
        render(<ChatView {...baseProps()} />);
        expect(within(screen.getByTestId('composer-chips'))
            .getByRole('button', { name: /Talking to: The Story Teller/ })).toBeTruthy();
    });

    it('asks an empty chat who to talk to, and passes the pick on', () => {
        const p = baseProps();
        render(<ChatView {...p} />);
        fireEvent.click(screen.getByRole('button', { name: /The Librarian.*Careful answers/ }));
        expect(p.setProfile).toHaveBeenCalledWith('p2');
    });

    it('has no cards once the chat has messages', () => {
        render(<ChatView {...baseProps({ messages: [{ id: 'u1', role: 'user', content: 'Hi' }] })} />);
        expect(screen.queryByText('Who would you like to talk to?')).toBeNull();
    });

    it('reads the profiles again when it opens', () => {
        const p = baseProps();
        render(<ChatView {...p} />);
        expect(p.refreshProfiles).toHaveBeenCalledTimes(1);
    });

    // Found in the walk at 360 px: centred content taller than the list was cut off at the top, out of scroll's reach.
    // jsdom has no layout, so this pins the rule: centre only while it fits ("safe" centring).
    it('keeps the top of an empty chat reachable when the cards make it taller than the screen', () => {
        render(<ChatView {...baseProps()} />);
        const empty = screen.getByText('Who would you like to talk to?').closest('div.h-full, div.min-h-full');
        expect(empty.className).toContain('justify-center-safe');
    });

    // Also from the walk: the list followed the bottom even when empty, so a phone opened on the last cards with the
    // question above it out of sight. An empty chat starts at its top; a chat with messages still follows the bottom.
    it('does not scroll an empty chat to its bottom', () => {
        const set = vi.fn();
        const desc = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop');
        Object.defineProperty(Element.prototype, 'scrollTop', { configurable: true, get: () => 0, set });
        try {
            const { unmount } = render(<ChatView {...baseProps()} />);
            expect(set).not.toHaveBeenCalled();
            unmount();
            render(<ChatView {...baseProps({ messages: [{ id: 'u1', role: 'user', content: 'Hi' }] })} />);
            expect(set).toHaveBeenCalled();
        } finally {
            if (desc) Object.defineProperty(Element.prototype, 'scrollTop', desc);
            else delete Element.prototype.scrollTop;
        }
    });
});
