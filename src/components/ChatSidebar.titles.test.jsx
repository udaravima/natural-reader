import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatSidebar from './ChatSidebar';

// Generate title (helper spec §6): beside Rename, on a chat the server holds, when a helper model is set up.
const theme = { bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '',
    textMuted: '', hover: '' };
const chat = (over = {}) => ({ id: 's-1', title: 'Budget questions', titleSource: 'person', titlePending: false,
    source: 'pg', messageCount: 2, updatedAt: Date.now(), ...over });
const props = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, sidebarOpen: true, selectedModel: 'ollama:m',
    setSelectedModel: vi.fn(), availableModels: [], reachable: true, refreshModels: vi.fn(), inferenceBudget: null,
    messages: [], clearHistory: vi.fn(), sessions: [chat()], activeSessionId: null, events: [], newSession: vi.fn(),
    switchToSession: vi.fn(), deleteSession: vi.fn(), renameSession: vi.fn(), helperOn: true,
    generateTitle: vi.fn(async () => true), ...over,
});

describe('ChatSidebar — Generate title', () => {
    it('offers Generate title on a server chat when the helper is on', () => {
        const p = props();
        render(<ChatSidebar {...p} />);
        fireEvent.click(screen.getByRole('button', { name: 'Generate title for Budget questions' }));
        expect(p.generateTitle).toHaveBeenCalledWith('s-1');
        expect(p.switchToSession).not.toHaveBeenCalled();
    });

    it('offers no Generate title with the helper off, or on a browser-only chat', () => {
        const { rerender } = render(<ChatSidebar {...props({ helperOn: false })} />);
        expect(screen.queryByRole('button', { name: /^Generate title for/ })).toBeNull();
        rerender(<ChatSidebar {...props({ sessions: [chat({ source: 'local' })] })} />);
        expect(screen.queryByRole('button', { name: /^Generate title for/ })).toBeNull();
    });

    it('disables Generate title while that chat says titlePending', () => {
        render(<ChatSidebar {...props({ sessions: [chat({ titlePending: true })] })} />);
        const btn = screen.getByRole('button', { name: 'Generate title for Budget questions' });
        expect(btn).toBeDisabled();
        expect(btn).toHaveAttribute('title', 'Writing a title…');
    });
});
