import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ProfileChip from './ProfileChip';

// Who answers in this chat (character profiles spec, "Choosing in the chat"): a chip beside "What this chat is about".
const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const STORY = { id: 'p1', name: 'The Story Teller', description: 'A wise old man', isDefault: true, builtin: false };
const LIB = { id: 'p2', name: 'The Librarian', description: 'Careful answers', isDefault: false, builtin: true };
const props = (over = {}) => ({ theme, profiles: [STORY, LIB], profileId: 'p1', currentProfile: null,
    removedProfile: null, onChange: vi.fn(), ...over });

describe('ProfileChip', () => {
    it('names who answers, and offers the profiles as a menu', () => {
        const p = props();
        render(<ProfileChip {...p} />);
        const chip = screen.getByRole('button', { name: /Talking to: The Story Teller/ });
        expect(chip.getAttribute('aria-haspopup')).toBe('menu');
        fireEvent.click(chip);
        const items = screen.getAllByRole('menuitemradio');
        expect(items.map((i) => i.textContent)).toEqual(['The Story Teller (default)', 'The Librarian']);
        expect(items[0].getAttribute('aria-checked')).toBe('true');
        fireEvent.click(items[1]);
        expect(p.onChange).toHaveBeenCalledWith('p2');
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('is plain text when there is nothing to choose', () => {
        render(<ProfileChip {...props({ profiles: [LIB], profileId: 'p2' })} />);
        expect(screen.getByText('Talking to: The Librarian')).toBeTruthy();
        expect(screen.queryByRole('button')).toBeNull();
    });

    it("lists the chat's own profile that was turned off, and names it", () => {
        render(<ProfileChip {...props({ profileId: 'p9', currentProfile: { id: 'p9', name: 'Legacy', removed: false } })} />);
        fireEvent.click(screen.getByRole('button', { name: /Talking to: Legacy/ }));
        expect(screen.getAllByRole('menuitemradio').map((i) => i.textContent)).toContain('Legacy (turned off)');
    });

    it('never shows an empty name: an unknown pick falls back to the default', () => {
        render(<ProfileChip {...props({ profileId: 'gone' })} />);
        expect(screen.getByRole('button', { name: /Talking to: The Story Teller/ })).toBeTruthy();
    });

    it('says when the profile this chat used was removed', () => {
        render(<ProfileChip {...props({ removedProfile: 'Tutor' })} />);
        expect(screen.getByText('The profile this chat used, Tutor, was removed.')).toBeTruthy();
    });

    it('renders nothing without profiles', () => {
        const { container } = render(<ProfileChip {...props({ profiles: [], profileId: '' })} />);
        expect(container.textContent).toBe('');
    });
});
