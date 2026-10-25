import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WebOfferCard from './WebOfferCard';
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts';

// In the reader the narration's keys are live on window: Space plays or
// pauses, Escape stops. The offer card in the chat beside the page must keep
// both for itself: Space presses "Search the web" or "Not now" (the card is
// marked data-own-keys), and Escape is not a request to stop the narration.
const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };

function Harness({ cb, onSearch }) {
    useKeyboardShortcuts({ ...cb, numPages: 3, viewMode: 'reader' });
    return <WebOfferCard theme={theme} reason="The files are silent." query="zephyr release" onSearch={onSearch} />;
}
const mount = () => {
    const cb = { handlePlayPause: vi.fn(), stopPlayback: vi.fn(), skipToNextSentence: vi.fn(),
        setCurrentSentenceIndex: vi.fn(), setCurrentPage: vi.fn(), setScale: vi.fn(), setDarkMode: vi.fn(), setDistractionFree: vi.fn() };
    const onSearch = vi.fn();
    render(<Harness cb={cb} onSearch={onSearch} />);
    return { cb, onSearch };
};

describe('WebOfferCard beside the reader (narration shortcuts live on window)', () => {
    it('Space on "Search the web" presses it and does not toggle the narration', async () => {
        const user = userEvent.setup();
        const { cb, onSearch } = mount();
        screen.getByRole('button', { name: 'Search the web' }).focus();
        await user.keyboard(' ');
        expect(onSearch).toHaveBeenCalledWith('zephyr release');
        expect(cb.handlePlayPause).not.toHaveBeenCalled();
    });

    it('Space on "Not now" presses it and does not toggle the narration', async () => {
        const user = userEvent.setup();
        const { cb } = mount();
        screen.getByRole('button', { name: 'Not now' }).focus();
        await user.keyboard(' ');
        expect(screen.queryByRole('region', { name: 'Web search offer' })).toBeNull();
        expect(cb.handlePlayPause).not.toHaveBeenCalled();
    });

    it('Escape on either button does not stop the narration', async () => {
        const user = userEvent.setup();
        const { cb } = mount();
        screen.getByRole('button', { name: 'Search the web' }).focus();
        await user.keyboard('{Escape}');
        screen.getByRole('button', { name: 'Not now' }).focus();
        await user.keyboard('{Escape}');
        expect(cb.stopPlayback).not.toHaveBeenCalled();
    });

    it('Space away from the card still toggles the narration', async () => {
        const user = userEvent.setup();
        const { cb } = mount();
        await user.click(document.body);
        await user.keyboard(' ');
        expect(cb.handlePlayPause).toHaveBeenCalledTimes(1);
    });
});
