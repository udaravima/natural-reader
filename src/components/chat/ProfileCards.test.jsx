import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ProfileCards from './ProfileCards';

// An empty chat asks who the person wants to talk to (character profiles spec, "Choosing in the chat").
const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const STORY = { id: 'p1', name: 'The Story Teller', description: 'A wise old man', isDefault: true };
const LIB = { id: 'p2', name: 'The Librarian', description: 'Careful answers', isDefault: false, builtin: true };

describe('ProfileCards', () => {
    it('offers each profile as a card, marks the current one, and passes a pick on', () => {
        const onPick = vi.fn();
        render(<ProfileCards theme={theme} profiles={[STORY, LIB]} profileId="p1" onPick={onPick} />);
        expect(screen.getByText('Who would you like to talk to?')).toBeTruthy();
        const story = screen.getByRole('button', { name: /The Story Teller/ });
        const lib = screen.getByRole('button', { name: /The Librarian/ });
        expect(story.getAttribute('aria-pressed')).toBe('true');
        expect(lib.getAttribute('aria-pressed')).toBe('false');
        expect(story.textContent).toContain('(default)');
        expect(lib.textContent).toContain('Careful answers');
        fireEvent.click(lib);
        expect(onPick).toHaveBeenCalledWith('p2');
    });

    it('shows nothing when there is no choice', () => {
        const { container } = render(<ProfileCards theme={theme} profiles={[LIB]} profileId="p2" onPick={vi.fn()} />);
        expect(container.textContent).toBe('');
    });
});
