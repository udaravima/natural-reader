import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProfileMenu from './ProfileMenu';
import HeaderOverflowMenu from './HeaderOverflowMenu';
import PdfToolbarMenu from './PdfToolbarMenu';
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts';

// In the reader the narration's keys are live on window: Space plays or pauses, Escape stops (walk finding 4).
// The reader header's three menus leave the focus on their opener when they open, so Escape (to close the
// menu) and Space (to press the opener) used to reach those shortcuts and stop or toggle the narration.
// Each menu's wrapper is data-own-keys now, and Escape gives the focus back to the opener.
const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };

const MENUS = {
    'profile menu': { opener: 'Account', render: () => <ProfileMenu theme={theme} user={{ email: 'me@x.io', display_name: 'Me' }} darkMode={false} setDarkMode={vi.fn()} onOpenSettings={vi.fn()} onLogout={vi.fn()} /> },
    'header overflow menu': { opener: 'More actions', render: () => <HeaderOverflowMenu theme={theme} darkMode={false} onShowShortcuts={vi.fn()} /> },
    'PDF toolbar menu': { opener: 'More actions', render: () => <PdfToolbarMenu theme={theme} actions={[{ key: 'fit', label: 'Fit width', onClick: vi.fn() }]} /> },
};

function Harness({ cb, children }) {
    useKeyboardShortcuts({ ...cb, numPages: 3, viewMode: 'reader' });
    return <div><button>page body</button>{children}</div>;
}
const mount = (menu) => {
    const cb = { handlePlayPause: vi.fn(), stopPlayback: vi.fn(), skipToNextSentence: vi.fn(),
        setCurrentSentenceIndex: vi.fn(), setCurrentPage: vi.fn(), setScale: vi.fn(), setDarkMode: vi.fn(), setDistractionFree: vi.fn() };
    render(<Harness cb={cb}>{menu.render()}</Harness>);
    return { cb, opener: screen.getByRole('button', { name: menu.opener }) };
};

describe.each(Object.entries(MENUS))('the %s beside the reader (narration shortcuts live on window)', (name, menu) => {
    it('Escape on the opener closes the menu and does not stop the narration', async () => {
        const user = userEvent.setup();
        const { cb, opener } = mount(menu);
        opener.focus();
        await user.keyboard('{Enter}');
        expect(screen.getByRole('menu')).toBeInTheDocument();
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('menu')).toBeNull();
        expect(cb.stopPlayback).not.toHaveBeenCalled();
        expect(document.activeElement).toBe(opener);
    });

    it('Space on the opener toggles the menu and does not play or pause the narration', async () => {
        const user = userEvent.setup();
        const { cb, opener } = mount(menu);
        opener.focus();
        await user.keyboard(' ');
        expect(screen.getByRole('menu')).toBeInTheDocument();
        await user.keyboard(' ');
        expect(screen.queryByRole('menu')).toBeNull();
        expect(cb.handlePlayPause).not.toHaveBeenCalled();
    });

    it('Escape on an item closes the menu, hands the focus back to the opener, and leaves the narration alone', async () => {
        const user = userEvent.setup();
        const { cb, opener } = mount(menu);
        opener.focus();
        await user.keyboard('{Enter}');
        await user.tab();                               // into the first item
        expect(screen.getByRole('menu').contains(document.activeElement)).toBe(true);
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('menu')).toBeNull();
        expect(document.activeElement).toBe(opener);
        expect(cb.stopPlayback).not.toHaveBeenCalled();
    });

    it('Escape from another field leaves the focus where it is', async () => {
        const user = userEvent.setup();
        const { opener } = mount(menu);
        await user.click(opener);                       // open by mouse
        screen.getByRole('button', { name: 'page body' }).focus();   // Tab-like move out; the menu stays open
        await user.keyboard('{Escape}');
        expect(document.activeElement).toBe(screen.getByRole('button', { name: 'page body' }));
    });

    it('a click elsewhere still closes the menu', async () => {
        const user = userEvent.setup();
        const { opener } = mount(menu);
        await user.click(opener);
        expect(screen.getByRole('menu')).toBeInTheDocument();
        await user.click(screen.getByRole('button', { name: 'page body' }));
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('the narration keys still work away from the menu', async () => {
        const user = userEvent.setup();
        const { cb } = mount(menu);
        screen.getByRole('button', { name: 'page body' }).focus();
        await user.keyboard('{Escape}');
        await user.keyboard(' ');
        expect(cb.stopPlayback).toHaveBeenCalledTimes(1);
        expect(cb.handlePlayPause).toHaveBeenCalledTimes(1);
    });
});
