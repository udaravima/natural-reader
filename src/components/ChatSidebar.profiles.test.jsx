import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatSidebar from './ChatSidebar';

// The profile picker (profile library spec, "For a person"): under Model, only when there is a choice.
const theme = { bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '',
    textMuted: '', hover: '' };
const PLAIN = { id: 'p1', name: 'Plain', description: 'Short answers', isDefault: true };
const TUTOR = { id: 'p2', name: 'Tutor', description: '', isDefault: false };
const props = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, sidebarOpen: true, selectedModel: 'ollama:m',
    setSelectedModel: vi.fn(), availableModels: [], reachable: true, refreshModels: vi.fn(), inferenceBudget: null,
    messages: [], clearHistory: vi.fn(), sessions: [], activeSessionId: null, events: [], newSession: vi.fn(),
    switchToSession: vi.fn(), deleteSession: vi.fn(), renameSession: vi.fn(), helperOn: false, generateTitle: vi.fn(),
    profiles: [PLAIN, TUTOR], profileId: 'p1', setProfile: vi.fn(), currentProfile: null, removedProfile: null, ...over,
});

describe('ChatSidebar — profile picker', () => {
    it('lists the profiles, marks the default, and shows the picked one\'s description', () => {
        render(<ChatSidebar {...props()} />);
        const picker = screen.getByRole('combobox', { name: 'Profile' });
        expect(picker.value).toBe('p1');
        expect([...picker.options].map((o) => o.textContent)).toEqual(['Plain (default)', 'Tutor']);
        expect(screen.getByText('Short answers')).toBeTruthy();
    });

    it('passes a pick on', () => {
        const p = props();
        render(<ChatSidebar {...p} />);
        fireEvent.change(screen.getByRole('combobox', { name: 'Profile' }), { target: { value: 'p2' } });
        expect(p.setProfile).toHaveBeenCalledWith('p2');
    });

    it('is hidden when there is nothing to choose', () => {
        const { rerender } = render(<ChatSidebar {...props({ profiles: [PLAIN] })} />);
        expect(screen.queryByRole('combobox', { name: 'Profile' })).toBeNull();
        rerender(<ChatSidebar {...props({ profiles: [], profileId: '' })} />);
        expect(screen.queryByRole('combobox', { name: 'Profile' })).toBeNull();
    });

    it("shows a chat's own profile that was turned off", () => {
        render(<ChatSidebar {...props({ profileId: 'p9', currentProfile: { id: 'p9', name: 'Legacy', removed: false } })} />);
        const picker = screen.getByRole('combobox', { name: 'Profile' });
        expect(picker.value).toBe('p9');
        expect([...picker.options].map((o) => o.textContent)).toContain('Legacy (turned off)');
    });

    it('says when the profile this chat used was removed', () => {
        render(<ChatSidebar {...props({ removedProfile: 'Tutor' })} />);
        expect(screen.getByText('The profile this chat used, Tutor, was removed.')).toBeTruthy();
    });
});
