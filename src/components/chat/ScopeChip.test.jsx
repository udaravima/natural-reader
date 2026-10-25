import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ScopeChip from './ScopeChip';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const NONE = { kind: 'none' };
const DOC = { kind: 'document' };
const project = (name, id = 'p1') => ({ kind: 'project', project: { id, name } });

function mount(over = {}) {
    const props = {
        theme, scope: NONE, openDocName: null, openDocProjects: [], onChange: vi.fn(),
        loadProjects: vi.fn(async () => []), ...over,
    };
    return { props, ...render(<ScopeChip {...props} />) };
}
// Every item of the open menu, whatever its menuitem role (scopes are radios, Choose a project… is a plain item).
const menuItems = () => [...screen.getByRole('menu').querySelectorAll('[role^="menuitem"]')];
const chip = () => screen.getByRole('button', { name: /No document|This document|Project/ });

describe('ScopeChip', () => {
    it('shows the current scope', () => {
        const { rerender, props } = mount();
        expect(chip().textContent).toBe('No document');
        rerender(<ScopeChip {...props} scope={DOC} openDocName="Thesis.pdf" />);
        expect(chip().textContent).toBe('This document: Thesis.pdf');
        rerender(<ScopeChip {...props} scope={project('Q3 Audit')} openDocName="Thesis.pdf" />);
        expect(chip().textContent).toBe('Project: Q3 Audit');
    });

    it('says "Project unavailable" for a project the reader can no longer see', () => {
        mount({ scope: { kind: 'project', project: { id: 'p1', name: null } } });
        expect(chip().textContent).toBe('Project unavailable');
    });

    it('is a menu button that reports whether the menu is open', () => {
        mount();
        expect(chip().getAttribute('aria-haspopup')).toBe('menu');
        expect(chip().getAttribute('aria-expanded')).toBe('false');
        expect(screen.queryByRole('menu')).toBeNull();
        fireEvent.click(chip());
        expect(chip().getAttribute('aria-expanded')).toBe('true');
        expect(screen.getByRole('menu')).toBeInTheDocument();
    });

    it('offers no document, the open document, and its projects', () => {
        mount({ openDocName: 'Thesis.pdf', openDocProjects: [{ id: 'p1', name: 'Q3 Audit' }, { id: 'p2', name: 'Board' }] });
        fireEvent.click(chip());
        const names = menuItems().map((i) => i.textContent);
        expect(names).toEqual(['No document', 'This document: Thesis.pdf', 'Q3 Audit', 'Board', 'Choose a project…']);
    });

    it("offers the open document's projects, and choosing one reports that project", () => {
        const { props } = mount({ openDocName: 'Thesis.pdf', openDocProjects: [{ id: 'p1', name: 'Q3 Audit' }] });
        fireEvent.click(chip());
        fireEvent.click(screen.getByRole('menuitemradio', { name: 'Q3 Audit' }));
        expect(props.onChange).toHaveBeenCalledWith({ kind: 'project', project: { id: 'p1', name: 'Q3 Audit' } });
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('offers "This document" only when a document is open', () => {
        mount({ openDocName: null });
        fireEvent.click(chip());
        expect(screen.queryByRole('menuitemradio', { name: /This document/ })).toBeNull();
        expect(menuItems().map((i) => i.textContent)).toEqual(['No document', 'Choose a project…']);
    });

    it('reports the document and none scopes', () => {
        const { props } = mount({ scope: project('Q3 Audit'), openDocName: 'Thesis.pdf' });
        fireEvent.click(chip());
        fireEvent.click(screen.getByRole('menuitemradio', { name: 'This document: Thesis.pdf' }));
        expect(props.onChange).toHaveBeenLastCalledWith({ kind: 'document' });
        fireEvent.click(chip());
        fireEvent.click(screen.getByRole('menuitemradio', { name: 'No document' }));
        expect(props.onChange).toHaveBeenLastCalledWith({ kind: 'none' });
    });

    it('labels an old version of the open document', () => {
        mount({ scope: DOC, openDocName: 'Thesis.pdf', openDocVersion: 2 });
        expect(chip().textContent).toBe('This document: Thesis.pdf · v2 (old version)');
    });

    it('marks the current scope as the checked radio item, and Choose a project… as a plain item', () => {
        mount({ scope: DOC, openDocName: 'Thesis.pdf', openDocProjects: [{ id: 'p1', name: 'Q3 Audit' }] });
        fireEvent.click(chip());
        expect(screen.getByRole('menuitemradio', { name: 'This document: Thesis.pdf' }).getAttribute('aria-checked')).toBe('true');
        expect(screen.getByRole('menuitemradio', { name: 'No document' }).getAttribute('aria-checked')).toBe('false');
        expect(screen.getByRole('menuitemradio', { name: 'Q3 Audit' }).getAttribute('aria-checked')).toBe('false');
        expect(screen.getByRole('menuitem', { name: 'Choose a project…' }).hasAttribute('aria-checked')).toBe(false);
        expect(document.querySelector('[aria-current]')).toBeNull();
    });

    it('checks the open document\'s project when it is the scope', () => {
        mount({ scope: project('Q3 Audit'), openDocName: 'Thesis.pdf', openDocProjects: [{ id: 'p1', name: 'Q3 Audit' }] });
        fireEvent.click(chip());
        expect(screen.getByRole('menuitemradio', { name: 'Q3 Audit' }).getAttribute('aria-checked')).toBe('true');
    });

    it('renders names as text, never as HTML', () => {
        const evil = '<img src=x onerror=alert(1)>';
        mount({ scope: project(evil), openDocName: '<b>Bold</b>.pdf', openDocProjects: [{ id: 'p9', name: '<i>Hi</i>' }] });
        expect(chip().textContent).toBe(`Project: ${evil}`);
        fireEvent.click(chip());
        expect(screen.getByRole('menuitemradio', { name: '<i>Hi</i>' })).toBeInTheDocument();
        expect(screen.getByRole('menuitemradio', { name: 'This document: <b>Bold</b>.pdf' })).toBeInTheDocument();
        expect(document.querySelector('img, i, b')).toBeNull();
    });

    describe('Choose a project…', () => {
        it('lists projects from the API by name, and loads only when chosen', async () => {
            const loadProjects = vi.fn(async () => [{ id: 'p1', name: 'Q3 Audit' }, { id: 'p2', name: 'Board' }]);
            const { props } = mount({ loadProjects });
            fireEvent.click(chip());
            expect(loadProjects).not.toHaveBeenCalled();
            fireEvent.click(screen.getByRole('menuitem', { name: 'Choose a project…' }));
            expect(loadProjects).toHaveBeenCalledTimes(1);
            const board = await screen.findByRole('menuitemradio', { name: 'Board' });
            expect(screen.getByRole('menuitemradio', { name: 'Q3 Audit' })).toBeInTheDocument();
            fireEvent.click(board);
            expect(props.onChange).toHaveBeenCalledWith({ kind: 'project', project: { id: 'p2', name: 'Board' } });
            expect(screen.queryByRole('menu')).toBeNull();
        });

        it('says "Loading projects…" while it loads', async () => {
            let finish;
            const loadProjects = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
            mount({ loadProjects });
            fireEvent.click(chip());
            fireEvent.click(screen.getByRole('menuitem', { name: 'Choose a project…' }));
            expect(screen.getByText('Loading projects…')).toBeInTheDocument();
            finish([{ id: 'p1', name: 'Q3 Audit' }]);
            await screen.findByRole('menuitemradio', { name: 'Q3 Audit' });
            expect(screen.queryByText('Loading projects…')).toBeNull();
        });

        it("says so when the reader isn't in any project", async () => {
            mount({ loadProjects: vi.fn(async () => []) });
            fireEvent.click(chip());
            fireEvent.click(screen.getByRole('menuitem', { name: 'Choose a project…' }));
            expect(await screen.findByText("You're not in any project yet.")).toBeInTheDocument();
        });

        it("says \"Couldn't load projects.\" on an error, and choosing again retries", async () => {
            const loadProjects = vi.fn()
                .mockRejectedValueOnce(new Error('offline'))
                .mockResolvedValueOnce([{ id: 'p1', name: 'Q3 Audit' }]);
            mount({ loadProjects });
            fireEvent.click(chip());
            fireEvent.click(screen.getByRole('menuitem', { name: 'Choose a project…' }));
            expect(await screen.findByText("Couldn't load projects.")).toBeInTheDocument();
            fireEvent.click(screen.getByRole('menuitem', { name: 'Choose a project…' }));
            expect(loadProjects).toHaveBeenCalledTimes(2);
            expect(await screen.findByRole('menuitemradio', { name: 'Q3 Audit' })).toBeInTheDocument();
            expect(screen.queryByText("Couldn't load projects.")).toBeNull();
        });

        it('ignores a load that finishes after the menu closed and reopened', async () => {
            let finish;
            const loadProjects = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
            mount({ loadProjects });
            fireEvent.click(chip());
            fireEvent.click(screen.getByRole('menuitem', { name: 'Choose a project…' }));
            fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
            finish([{ id: 'p1', name: 'Q3 Audit' }]);
            fireEvent.click(chip());
            expect(screen.queryByRole('menuitemradio', { name: 'Q3 Audit' })).toBeNull();   // a fresh menu starts from the top
            expect(screen.queryByText('Loading projects…')).toBeNull();
        });
    });

    describe('keyboard', () => {
        it('Enter opens the menu on the first item, arrows move, Enter chooses', async () => {
            const user = userEvent.setup();
            const { props } = mount({ scope: project('Q3 Audit'), openDocName: 'Thesis.pdf' });
            await user.tab();
            expect(chip()).toHaveFocus();
            await user.keyboard('{Enter}');
            expect(chip().getAttribute('aria-expanded')).toBe('true');
            const items = menuItems();
            expect(items[0]).toHaveFocus();
            await user.keyboard('{ArrowDown}');
            expect(items[1]).toHaveFocus();
            await user.keyboard('{ArrowDown}');
            expect(items[2]).toHaveFocus();
            await user.keyboard('{ArrowDown}');                  // wraps from the last item to the first
            expect(items[0]).toHaveFocus();
            await user.keyboard('{ArrowUp}');                    // and back round
            expect(items[2]).toHaveFocus();
            await user.keyboard('{Home}');
            expect(items[0]).toHaveFocus();
            await user.keyboard('{End}');
            expect(items[2]).toHaveFocus();
            await user.keyboard('{ArrowUp}{Enter}');
            expect(props.onChange).toHaveBeenCalledWith({ kind: 'document' });
            expect(screen.queryByRole('menu')).toBeNull();
            expect(chip()).toHaveFocus();                        // focus comes back to the chip
        });

        it('Space opens the menu too', async () => {
            const user = userEvent.setup();
            mount();
            await user.tab();
            await user.keyboard(' ');
            expect(screen.getByRole('menu')).toBeInTheDocument();
        });

        it('ArrowDown on the chip opens the menu', async () => {
            const user = userEvent.setup();
            mount();
            await user.tab();
            await user.keyboard('{ArrowDown}');
            expect(screen.getByRole('menu')).toBeInTheDocument();
            expect(menuItems()[0]).toHaveFocus();
        });

        it('Escape closes the menu without choosing, and returns focus to the chip', async () => {
            const user = userEvent.setup();
            const { props } = mount();
            await user.tab();
            await user.keyboard('{Enter}');
            await user.keyboard('{Escape}');
            expect(screen.queryByRole('menu')).toBeNull();
            expect(chip().getAttribute('aria-expanded')).toBe('false');
            expect(chip()).toHaveFocus();
            expect(props.onChange).not.toHaveBeenCalled();
        });

        it('starts on the current scope when it opens', async () => {
            const user = userEvent.setup();
            mount({ scope: DOC, openDocName: 'Thesis.pdf' });
            await user.tab();
            await user.keyboard('{Enter}');
            expect(screen.getByRole('menuitemradio', { name: 'This document: Thesis.pdf' })).toHaveFocus();
        });

        it('arrow keys reach the projects that Choose a project… loads', async () => {
            const user = userEvent.setup();
            mount({ loadProjects: vi.fn(async () => [{ id: 'p1', name: 'Q3 Audit' }]) });
            await user.tab();
            await user.keyboard('{Enter}{ArrowDown}{Enter}');   // No document → Choose a project…
            const loaded = await screen.findByRole('menuitemradio', { name: 'Q3 Audit' });
            expect(screen.getByRole('menuitem', { name: 'Choose a project…' })).toHaveFocus();   // the menu stays put
            await user.keyboard('{ArrowDown}');
            expect(loaded).toHaveFocus();
        });
    });

    it('closes when the reader clicks elsewhere', async () => {
        render(<div><p>elsewhere</p><ScopeChip theme={theme} scope={NONE} openDocProjects={[]} onChange={vi.fn()} loadProjects={vi.fn()} /></div>);
        fireEvent.click(chip());
        expect(screen.getByRole('menu')).toBeInTheDocument();
        fireEvent.mouseDown(screen.getByText('elsewhere'));
        await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    });

    it('keeps the menu inside its own box (status lines are outside the menu role)', async () => {
        mount({ loadProjects: vi.fn(async () => []) });
        fireEvent.click(chip());
        fireEvent.click(screen.getByRole('menuitem', { name: 'Choose a project…' }));
        const status = await screen.findByText("You're not in any project yet.");
        expect(within(screen.getByRole('menu')).queryByText("You're not in any project yet.")).toBeNull();
        expect(status).toBeInTheDocument();
    });
});
