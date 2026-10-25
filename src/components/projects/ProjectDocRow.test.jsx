import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProjectDocRow from './ProjectDocRow';

// The row's ⋯ menu (walk finding 5): it must close with Escape and with a click elsewhere, and its button
// must say whether it is open. Before, only pressing ⋯ again closed it.
const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const can = { edit: false, manage_members: false, manage_owners: false, file_docs: true, remove_docs: true, delete: false, leave: true };
const doc = { id: 'x', name: 'report.pdf', version: 2, versions_count: 2, doc_id: 'h2', state: 'indexed' };
const other = { id: 'y', name: 'report-final.pdf', version: 1, versions_count: 1, doc_id: 'h3', state: 'indexed' };

function mount() {
    render(
        <div>
            <button>elsewhere</button>
            <ProjectDocRow theme={theme} api={{}} project={{ id: 'p1', name: 'Q3', can }} doc={doc} others={[other]}
                mine={[]} busy={false} run={vi.fn()} onOpenDoc={vi.fn()} />
        </div>,
    );
    return screen.getByRole('button', { name: 'More actions for report.pdf' });
}

describe('ProjectDocRow ⋯ menu', () => {
    it('says whether it is open, on the button', async () => {
        const user = userEvent.setup();
        const opener = mount();
        expect(opener.getAttribute('aria-expanded')).toBe('false');
        await user.click(opener);
        expect(opener.getAttribute('aria-expanded')).toBe('true');
        expect(screen.getByRole('menu')).toBeInTheDocument();
        await user.click(opener);
        expect(opener.getAttribute('aria-expanded')).toBe('false');
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('Escape closes it and puts the focus back on the button', async () => {
        const user = userEvent.setup();
        const opener = mount();
        await user.click(opener);
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('menu')).toBeNull();
        expect(opener.getAttribute('aria-expanded')).toBe('false');
        expect(document.activeElement).toBe(opener);
    });

    it('Escape closes it from an item too, and the focus lands on the button', async () => {
        const user = userEvent.setup();
        const opener = mount();
        await user.click(opener);
        await user.tab();                       // into the first item
        expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Upload new version' }));
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('menu')).toBeNull();
        expect(document.activeElement).toBe(opener);
    });

    it('a click elsewhere closes it', async () => {
        const user = userEvent.setup();
        const opener = mount();
        await user.click(opener);
        await user.click(screen.getByRole('button', { name: 'elsewhere' }));
        expect(screen.queryByRole('menu')).toBeNull();
        expect(opener.getAttribute('aria-expanded')).toBe('false');
    });

    it('a click on another control of the same row (Remove) closes it too', async () => {
        const user = userEvent.setup();
        const opener = mount();
        await user.click(opener);
        await user.click(screen.getByRole('button', { name: 'Remove report.pdf from Q3' }));
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('a click inside the menu does not close it before the item acts', async () => {
        const opener = mount();
        fireEvent.click(opener);
        const menu = screen.getByRole('menu');
        fireEvent.mouseDown(menu);
        expect(screen.getByRole('menu')).toBeInTheDocument();
        fireEvent.mouseDown(opener);
        expect(screen.getByRole('menu')).toBeInTheDocument();   // the button's own click toggles it, not the outside handler
    });

    it('choosing an item still closes it and opens that panel', async () => {
        const user = userEvent.setup();
        mount();
        await user.click(screen.getByRole('button', { name: 'More actions for report.pdf' }));
        await user.click(screen.getByRole('menuitem', { name: 'Upload new version' }));
        expect(screen.queryByRole('menu')).toBeNull();
        expect(screen.getByLabelText('What changed?')).toBeInTheDocument();
    });

    it('Escape with the menu closed does nothing', async () => {
        const user = userEvent.setup();
        mount();
        await user.click(screen.getByRole('button', { name: 'elsewhere' }));
        await user.keyboard('{Escape}');
        expect(document.activeElement).toBe(screen.getByRole('button', { name: 'elsewhere' }));
    });
});
