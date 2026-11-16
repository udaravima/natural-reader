import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatSidebar from './ChatSidebar';

// The profile is chosen where the person chats (character profiles spec): the sidebar no longer has a picker.
const theme = { bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '',
    textMuted: '', hover: '' };
const props = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, sidebarOpen: true, selectedModel: 'ollama:m',
    setSelectedModel: vi.fn(), availableModels: [], reachable: true, refreshModels: vi.fn(), inferenceBudget: null,
    messages: [], clearHistory: vi.fn(), sessions: [], activeSessionId: null, events: [], newSession: vi.fn(),
    switchToSession: vi.fn(), deleteSession: vi.fn(), renameSession: vi.fn(), helperOn: false, generateTitle: vi.fn(),
    // What App used to hand it for the picker: even given two profiles, it shows none.
    profiles: [{ id: 'p1', name: 'Plain', description: '', isDefault: true }, { id: 'p2', name: 'Tutor', description: '' }],
    profileId: 'p1', setProfile: vi.fn(), currentProfile: null, removedProfile: 'Old', ...over,
});

describe('ChatSidebar — no profile picker', () => {
    it('has no Profile control', () => {
        render(<ChatSidebar {...props()} />);
        expect(screen.queryByRole('combobox', { name: 'Profile' })).toBeNull();
        expect(screen.queryByText('PROFILE')).toBeNull();
        expect(screen.queryByText(/The profile this chat used/)).toBeNull();
    });
});
