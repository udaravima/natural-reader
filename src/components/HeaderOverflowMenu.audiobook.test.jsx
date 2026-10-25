import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import HeaderOverflowMenu from './HeaderOverflowMenu';

const theme = { border: '', bgSecondary: '', bgTertiary: '', hover: '', text: '', textSecondary: '', textMuted: '' };
const base = (over = {}) => ({
    theme, darkMode: false, isLocalhost: true, setIsLocalhost: vi.fn(), setDarkMode: vi.fn(),
    onDownloadBookAudio: vi.fn(), onCancelBookDownload: vi.fn(), hasDocument: true, ...over,
});

// Below the lg breakpoint this menu is the only audiobook control: it used to
// show the progress with no way to cancel it.
describe('overflow menu audiobook', () => {
    it('offers Cancel while an audiobook is being made', () => {
        const props = base({ bookProgress: { current: 2, total: 9, label: 'Synthesizing page 3…' } });
        render(<HeaderOverflowMenu {...props} />);
        fireEvent.click(screen.getByTitle('More actions'));
        expect(screen.getByText('Audiobook 2/9')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('menuitem', { name: /cancel audiobook/i }));
        expect(props.onCancelBookDownload).toHaveBeenCalled();
    });

    it('shows no count while it is still reading the document', () => {
        render(<HeaderOverflowMenu {...base({ bookProgress: { current: 0, total: null, label: 'Reading the document…' } })} />);
        fireEvent.click(screen.getByTitle('More actions'));
        expect(screen.getByText('Making audiobook')).toBeInTheDocument();
        expect(screen.queryByText(/null/)).toBeNull();
    });

    it('offers no Cancel when nothing is running', () => {
        render(<HeaderOverflowMenu {...base({ bookProgress: null })} />);
        fireEvent.click(screen.getByTitle('More actions'));
        expect(screen.queryByRole('menuitem', { name: /cancel audiobook/i })).toBeNull();
    });
});
