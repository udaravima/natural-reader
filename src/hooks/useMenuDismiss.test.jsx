import { describe, it, expect, vi, afterEach } from 'vitest';
import { useRef, useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useMenuDismiss } from './useMenuDismiss';

// A bare menu: an opener and a panel inside one wrapper, and a field outside it.
function Menu() {
    const [open, setOpen] = useState(false);
    const wrapRef = useRef(null);
    const openerRef = useRef(null);
    useMenuDismiss({ open, onClose: () => setOpen(false), wrapRef, openerRef });
    return (
        <div>
            <input aria-label="elsewhere" />
            <div ref={wrapRef}>
                <button ref={openerRef} onClick={() => setOpen((v) => !v)}>opener</button>
                {open && <div role="menu"><button role="menuitem">item</button></div>}
            </div>
        </div>
    );
}
const opener = () => screen.getByRole('button', { name: 'opener' });

afterEach(() => { vi.restoreAllMocks(); });

describe('useMenuDismiss', () => {
    it('Escape that closes the menu does not go on to window, where the reader shortcuts listen', async () => {
        const user = userEvent.setup();
        const onWindow = vi.fn();
        window.addEventListener('keydown', onWindow);
        try {
            render(<Menu />);
            await user.click(opener());
            await user.keyboard('{Escape}');
            expect(screen.queryByRole('menu')).toBeNull();
            expect(onWindow).not.toHaveBeenCalled();
        } finally {
            window.removeEventListener('keydown', onWindow);
        }
    });

    it('Escape with the menu closed goes on to window', async () => {
        const user = userEvent.setup();
        const onWindow = vi.fn();
        window.addEventListener('keydown', onWindow);
        try {
            render(<Menu />);
            await user.keyboard('{Escape}');
            expect(onWindow).toHaveBeenCalledTimes(1);
        } finally {
            window.removeEventListener('keydown', onWindow);
        }
    });

    it('another key with the menu open goes on to window and leaves the menu open', async () => {
        const user = userEvent.setup();
        const onWindow = vi.fn();
        window.addEventListener('keydown', onWindow);
        try {
            render(<Menu />);
            await user.click(opener());
            await user.keyboard('x');
            expect(screen.getByRole('menu')).toBeInTheDocument();
            expect(onWindow).toHaveBeenCalledTimes(1);
        } finally {
            window.removeEventListener('keydown', onWindow);
        }
    });

    describe('where the focus goes after Escape', () => {
        it('stays on the opener when it was there', async () => {
            const user = userEvent.setup();
            render(<Menu />);
            await user.click(opener());
            await user.keyboard('{Escape}');
            expect(document.activeElement).toBe(opener());
        });

        it('goes to the opener from an item inside the menu', async () => {
            const user = userEvent.setup();
            render(<Menu />);
            await user.click(opener());
            screen.getByRole('menuitem').focus();
            await user.keyboard('{Escape}');
            expect(document.activeElement).toBe(opener());
        });

        it('goes to the opener when the focus is on the page (<body>)', async () => {
            const user = userEvent.setup();
            render(<Menu />);
            await user.click(opener());
            opener().blur();
            expect(document.activeElement).toBe(document.body);
            await user.keyboard('{Escape}');
            expect(document.activeElement).toBe(opener());
        });

        it('goes to the opener when the browser reports no focused element at all', async () => {
            const user = userEvent.setup();
            render(<Menu />);
            await user.click(opener());
            const focus = vi.spyOn(opener(), 'focus');
            vi.spyOn(document, 'activeElement', 'get').mockReturnValue(null);
            fireEvent.keyDown(document.body, { key: 'Escape' });
            expect(focus).toHaveBeenCalledTimes(1);
            expect(screen.queryByRole('menu')).toBeNull();
        });

        it('does not move when the focus is in another field', async () => {
            const user = userEvent.setup();
            render(<Menu />);
            await user.click(opener());
            screen.getByRole('textbox', { name: 'elsewhere' }).focus();
            await user.keyboard('{Escape}');
            expect(screen.queryByRole('menu')).toBeNull();
            expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'elsewhere' }));
        });
    });

    it('a mouse press outside closes the menu; one inside does not', async () => {
        const user = userEvent.setup();
        render(<Menu />);
        await user.click(opener());
        fireEvent.mouseDown(screen.getByRole('menuitem'));
        expect(screen.getByRole('menu')).toBeInTheDocument();
        fireEvent.mouseDown(screen.getByRole('textbox', { name: 'elsewhere' }));
        expect(screen.queryByRole('menu')).toBeNull();
    });
});
