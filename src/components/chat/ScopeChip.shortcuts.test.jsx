import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ScopeChip from './ScopeChip';
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts';

// In the reader the narration's keys are live on window: Space plays or
// pauses, Escape stops. A chip in the chat beside the page must keep both for
// itself: Space opens the menu or picks the item, Escape closes the menu.
const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };

function Harness({ cb, onChange }) {
    useKeyboardShortcuts({ ...cb, numPages: 3, viewMode: 'reader' });
    return <ScopeChip theme={theme} scope={{ kind: 'none' }} openDocName="Thesis.pdf" openDocProjects={[]} onChange={onChange} loadProjects={vi.fn()} />;
}
const mount = () => {
    const cb = { handlePlayPause: vi.fn(), stopPlayback: vi.fn(), skipToNextSentence: vi.fn(),
        setCurrentSentenceIndex: vi.fn(), setCurrentPage: vi.fn(), setScale: vi.fn(), setDarkMode: vi.fn(), setDistractionFree: vi.fn() };
    const onChange = vi.fn();
    render(<Harness cb={cb} onChange={onChange} />);
    return { cb, onChange };
};

describe('ScopeChip beside the reader (narration shortcuts live on window)', () => {
    it('Space on the chip opens the menu and does not toggle the narration', async () => {
        const user = userEvent.setup();
        const { cb } = mount();
        await user.tab();
        await user.keyboard(' ');
        expect(screen.getByRole('menu')).toBeInTheDocument();
        expect(cb.handlePlayPause).not.toHaveBeenCalled();
    });

    it('Escape closes the menu and does not stop the narration', async () => {
        const user = userEvent.setup();
        const { cb } = mount();
        await user.tab();
        await user.keyboard('{Enter}');
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('menu')).toBeNull();
        expect(cb.stopPlayback).not.toHaveBeenCalled();
    });

    it('Escape on the chip itself leaves the narration alone too', async () => {
        const user = userEvent.setup();
        const { cb } = mount();
        await user.tab();
        await user.keyboard('{Escape}');
        expect(cb.stopPlayback).not.toHaveBeenCalled();
    });

    it('Space on a menu item picks it and does not toggle the narration', async () => {
        const user = userEvent.setup();
        const { cb, onChange } = mount();
        await user.tab();
        await user.keyboard('{Enter}{ArrowDown}');
        await user.keyboard(' ');
        expect(onChange).toHaveBeenCalledWith({ kind: 'document' });
        expect(cb.handlePlayPause).not.toHaveBeenCalled();
    });

    it('Space away from the chip still toggles the narration', async () => {
        const user = userEvent.setup();
        const { cb } = mount();
        await user.click(document.body);
        await user.keyboard(' ');
        expect(cb.handlePlayPause).toHaveBeenCalledTimes(1);
    });
});
