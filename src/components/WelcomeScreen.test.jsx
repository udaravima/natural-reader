import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import WelcomeScreen from './WelcomeScreen';

const theme = { canvasBg: '', bgTertiary: '', textMuted: '', border: '', textSecondary: '', borderSecondary: '', hover: '', text: '' };
const books = [{ fileName: 'Thesis.pdf', size: 1048576, lastOpened: 0, fileType: 'pdf' }];

const mount = (over = {}) => {
    const props = { theme, fileInputRef: { current: { click: vi.fn() } }, recentBooks: books,
        openFromLibrary: vi.fn(), removeFromLibrary: vi.fn(), openFolder: null, ...over };
    render(<WelcomeScreen {...props} />);
    return props;
};

describe('WelcomeScreen — a recent-book row', () => {
    afterEach(() => vi.restoreAllMocks());

    it('has no <button> inside a <button>', () => {
        const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
        mount();
        const row = screen.getByRole('button', { name: /Open Thesis/ });
        expect(row.tagName).not.toBe('BUTTON');
        expect(row.querySelector('button')).not.toBeNull(); // the remove control
        expect(errors.mock.calls.flat().join(' ')).not.toMatch(/descendant of <button>|validateDOMNesting/);
    });

    it('opens on click, Enter and Space', () => {
        const { openFromLibrary } = mount();
        const row = screen.getByRole('button', { name: /Open Thesis/ });
        fireEvent.click(row);
        fireEvent.keyDown(row, { key: 'Enter' });
        fireEvent.keyDown(row, { key: ' ' });
        expect(openFromLibrary).toHaveBeenCalledTimes(3);
        expect(openFromLibrary).toHaveBeenCalledWith('Thesis.pdf');
        expect(row).toHaveAttribute('tabIndex', '0');
    });

    it('the remove control removes without opening, by click or keyboard', () => {
        const { openFromLibrary, removeFromLibrary } = mount();
        const remove = screen.getByRole('button', { name: 'Remove Thesis.pdf from library' });
        fireEvent.click(remove);
        fireEvent.keyDown(remove, { key: 'Enter' });
        expect(removeFromLibrary).toHaveBeenCalledWith('Thesis.pdf', expect.anything());
        expect(openFromLibrary).not.toHaveBeenCalled();
    });
});
