import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('../lib/notifications', () => ({
    fetchInbox: vi.fn(), markRead: vi.fn(), markAllRead: vi.fn(),
}));
import { fetchInbox, markRead, markAllRead } from '../lib/notifications';
import NotificationBell from './NotificationBell';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const AT = new Date(Date.now() - 5 * 60_000).toISOString();

const single = (over = {}) => ({
    kind: 'doc.indexed', level: 'info', needs_action: false, unread: true, title: 'Q3.pdf finished indexing.',
    body: null, at: AT, count: 1, resolved: 0, ids: [1], more: 0, target: { doc: 'd1', name: 'Q3.pdf' },
    items: [{ id: 1, title: 'Q3.pdf finished indexing.', body: null, at: AT, unread: true, resolved: false, repeats: 1, target: { doc: 'd1', name: 'Q3.pdf' } }],
    ...over,
});

const group = (over = {}) => ({
    kind: 'doc.index_failed', level: 'action', needs_action: true, unread: true, title: '3 documents couldn\'t be indexed',
    body: null, at: AT, count: 3, resolved: 1, ids: [7, 6, 5], more: 0, target: null,
    items: [5, 6, 7].reverse().map((id) => ({
        id, title: `Couldn't index doc${id}.pdf.`, body: 'No text found', at: AT, unread: true, resolved: id === 5,
        repeats: 1, target: { doc: `d${id}`, name: `doc${id}.pdf` },
    })),
    ...over,
});

function setup(props = {}, rows = [group(), single()]) {
    fetchInbox.mockResolvedValue({ rows, total: rows.length });
    const onRefresh = vi.fn();
    const onOpenTarget = vi.fn();
    render(<NotificationBell theme={theme} apiHost="" apiPort="" count={2} action={1}
        onRefresh={onRefresh} onOpenTarget={onOpenTarget} {...props} />);
    return { onRefresh, onOpenTarget };
}

const bell = () => screen.getByRole('button', { name: /^notifications/i });
const openPanel = async () => {
    fireEvent.click(bell());
    return screen.findByRole('dialog', { name: 'Notifications' });
};

describe('NotificationBell', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        fetchInbox.mockReset();     // a test's own implementation or unused answers must not reach the next
        markRead.mockResolvedValue(true);
        markAllRead.mockResolvedValue(true);
    });

    it('the bell says how many unread rows and how many need action', () => {
        setup({ count: 3, action: 1 });
        expect(bell()).toHaveAccessibleName('Notifications, 3 unread, 1 needs action');
    });

    it('says only "Notifications" with nothing unread, and shows no badge', () => {
        setup({ count: 0, action: 0 });
        expect(bell()).toHaveAccessibleName('Notifications');
        expect(document.querySelector('[data-badge]')).toBeNull();
    });

    it('shows 99+ above 99', () => {
        setup({ count: 120, action: 0 });
        expect(document.querySelector('[data-badge]').textContent).toBe('99+');
    });

    it('the badge uses the warning colour only when something needs action', () => {
        const { unmount } = render(<NotificationBell theme={theme} count={2} action={1} onRefresh={vi.fn()} onOpenTarget={vi.fn()} />);
        expect(document.querySelector('[data-badge]').dataset.level).toBe('action');
        unmount();
        render(<NotificationBell theme={theme} count={2} action={0} onRefresh={vi.fn()} onOpenTarget={vi.fn()} />);
        expect(document.querySelector('[data-badge]').dataset.level).toBe('info');
    });

    it('opening the list fetches it and marks nothing read', async () => {
        setup();
        const panel = await openPanel();
        await within(panel).findByText('3 documents couldn\'t be indexed');
        expect(fetchInbox).toHaveBeenCalledWith('', '', { offset: 0, limit: 30 });
        expect(markRead).not.toHaveBeenCalled();
        expect(markAllRead).not.toHaveBeenCalled();
    });

    it('rows come in the server\'s order, unread heavier than read, resolved counted', async () => {
        setup({}, [group(), single({ unread: false, ids: [2], items: [{ ...single().items[0], id: 2, unread: false }] })]);
        const panel = await openPanel();
        const rows = await within(panel).findAllByRole('listitem');
        expect(rows[0]).toHaveTextContent('3 documents couldn\'t be indexed');
        expect(rows[0]).toHaveTextContent('1 resolved');
        expect(rows[0].dataset.unread).toBe('true');
        expect(rows[1].dataset.unread).toBe('false');
    });

    it('a single resolved notice is tagged Resolved', async () => {
        setup({}, [single({ kind: 'doc.index_failed', level: 'action', needs_action: false, resolved: 1, title: 'Couldn\'t index Q3.pdf.' })]);
        const panel = await openPanel();
        expect(await within(panel).findByText('Resolved')).toBeInTheDocument();
    });

    it('Mark all as read is disabled with nothing unread', async () => {
        setup({ count: 0, action: 0 }, [single({ unread: false })]);
        const panel = await openPanel();
        await within(panel).findByText('Q3.pdf finished indexing.');
        expect(within(panel).getByRole('button', { name: 'Mark all as read' })).toBeDisabled();
    });

    it('Mark all as read calls read-all, then refreshes the bell and the list', async () => {
        const { onRefresh } = setup();
        const panel = await openPanel();
        await within(panel).findByText('Q3.pdf finished indexing.');
        fireEvent.click(within(panel).getByRole('button', { name: 'Mark all as read' }));
        await waitFor(() => expect(markAllRead).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(onRefresh).toHaveBeenCalled());
        expect(fetchInbox).toHaveBeenCalledTimes(2);
    });

    it('a single row marks itself read, opens its target and closes the panel', async () => {
        const { onRefresh, onOpenTarget } = setup();
        const panel = await openPanel();
        fireEvent.click(await within(panel).findByRole('button', { name: /Q3\.pdf finished indexing/ }));
        await waitFor(() => expect(onOpenTarget).toHaveBeenCalledWith({ doc: 'd1', name: 'Q3.pdf' }));
        expect(markRead).toHaveBeenCalledWith('', '', [1]);
        expect(onRefresh).toHaveBeenCalled();
        expect(screen.queryByRole('dialog', { name: 'Notifications' })).toBeNull();
    });

    it('a group marks all its ids read and expands in place; a member opens its own target', async () => {
        const { onOpenTarget } = setup();
        const panel = await openPanel();
        const toggle = await within(panel).findByRole('button', { name: /3 documents couldn't be indexed/ });
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        fireEvent.click(toggle);
        await waitFor(() => expect(markRead).toHaveBeenCalledWith('', '', [7, 6, 5]));
        expect(toggle).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByRole('dialog', { name: 'Notifications' })).toBeInTheDocument();
        fireEvent.click(within(panel).getByRole('button', { name: /Couldn't index doc6\.pdf/ }));
        expect(onOpenTarget).toHaveBeenCalledWith({ doc: 'd6', name: 'doc6.pdf' });
    });

    it('clicking the group again collapses it, and a read group isn\'t marked again', async () => {
        setup({}, [group({ unread: false })]);
        const panel = await openPanel();
        const toggle = await within(panel).findByRole('button', { name: /3 documents couldn't be indexed/ });
        fireEvent.click(toggle);
        expect(toggle).toHaveAttribute('aria-expanded', 'true');
        fireEvent.click(toggle);
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        expect(markRead).not.toHaveBeenCalled();
    });

    it('says how many more a big group has', async () => {
        setup({}, [group({ count: 60, more: 10 })]);
        const panel = await openPanel();
        fireEvent.click(await within(panel).findByRole('button', { name: /couldn't be indexed/ }));
        expect(await within(panel).findByText('and 10 more')).toBeInTheDocument();
    });

    const older = (id, title) => single({ title, ids: [id], unread: false, items: [{ ...single().items[0], id, title, unread: false }] });

    it('Show more adds the rows not shown yet', async () => {
        fetchInbox.mockResolvedValueOnce({ rows: [single()], total: 31 })
            .mockResolvedValueOnce({ rows: [single(), older(9, 'Older one')], total: 2 });
        render(<NotificationBell theme={theme} apiHost="" apiPort="" count={1} action={0} onRefresh={vi.fn()} onOpenTarget={vi.fn()} />);
        const panel = await openPanel();
        fireEvent.click(await within(panel).findByRole('button', { name: 'Show more' }));
        expect(await within(panel).findByText('Older one')).toBeInTheDocument();
        // It asks from the top again: the list moves under it (a read row sinks, a new one arrives), so an
        // offset into it would skip a row or show one twice.
        expect(fetchInbox).toHaveBeenLastCalledWith('', '', { offset: 0, limit: 100 });
        expect(within(panel).getAllByText('Q3.pdf finished indexing.')).toHaveLength(1);
        expect(within(panel).queryByRole('button', { name: 'Show more' })).toBeNull();
    });

    it('Show more neither skips a row nor repeats one when the list moved', async () => {
        // Shown: A (unread). The person reads A, so the server now sorts it after B; a new N has arrived on top.
        const A = single({ title: 'A', ids: [1] });
        const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
        fetchInbox.mockResolvedValueOnce({ rows: [A], total: 2 })
            .mockResolvedValueOnce({ rows: [older(3, 'N'), older(2, 'B'), { ...A, unread: false }], total: 3 });
        render(<NotificationBell theme={theme} apiHost="" apiPort="" count={1} action={0} onRefresh={vi.fn()} onOpenTarget={vi.fn()} />);
        const panel = await openPanel();
        fireEvent.click(await within(panel).findByRole('button', { name: 'Show more' }));
        await within(panel).findByText('B');
        const titles = within(panel).getAllByRole('listitem').map((li) => li.querySelector('span.block').textContent);
        expect(titles).toEqual(['A', 'N', 'B']);
        expect(errors.mock.calls.filter((c) => String(c[0]).includes('same key'))).toEqual([]);
        errors.mockRestore();
    });

    it('Show more reaches every row of a long list exactly once', async () => {
        const all = Array.from({ length: 131 }, (_, i) => older(1000 + i, `Row ${i}`));
        fetchInbox.mockImplementation(async (_h, _p, { offset, limit }) => ({ rows: all.slice(offset, offset + limit), total: all.length }));
        render(<NotificationBell theme={theme} apiHost="" apiPort="" count={0} action={0} onRefresh={vi.fn()} onOpenTarget={vi.fn()} />);
        const panel = await openPanel();
        for (const shown of [60, 90, 120, 131]) {
            fireEvent.click(await within(panel).findByRole('button', { name: 'Show more' }));
            await waitFor(() => expect(within(panel).getAllByRole('listitem')).toHaveLength(shown));
        }
        // By the fourth click the first page of the walk held only rows already shown, so it read a second one.
        expect(fetchInbox).toHaveBeenLastCalledWith('', '', { offset: 100, limit: 100 });
        const titles = within(panel).getAllByRole('listitem').map((li) => li.querySelector('span.block').textContent);
        expect(titles).toEqual(all.map((r) => r.title));
        expect(within(panel).queryByRole('button', { name: 'Show more' })).toBeNull();
    });

    it('a list that answers after the panel was closed and reopened is dropped', async () => {
        let answerFirst;
        fetchInbox.mockReturnValueOnce(new Promise((resolve) => { answerFirst = resolve; }))
            .mockResolvedValueOnce({ rows: [single({ title: 'Fresh' })], total: 1 });
        render(<NotificationBell theme={theme} apiHost="" apiPort="" count={1} action={0} onRefresh={vi.fn()} onOpenTarget={vi.fn()} />);
        fireEvent.click(bell());      // open: the first request hangs
        fireEvent.click(bell());      // close
        const panel = await openPanel();
        await within(panel).findByText('Fresh');
        answerFirst({ rows: [single({ title: 'Stale' })], total: 1 });
        await waitFor(() => expect(within(panel).queryByText('Stale')).toBeNull());
        expect(within(panel).getByText('Fresh')).toBeInTheDocument();
    });

    it('Escape closes the panel and returns focus to the bell', async () => {
        setup();
        const panel = await openPanel();
        await within(panel).findByText('Q3.pdf finished indexing.');
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('dialog', { name: 'Notifications' })).toBeNull();
        expect(bell()).toHaveFocus();
    });

    it('says Nothing new. when empty', async () => {
        setup({ count: 0, action: 0 }, []);
        const panel = await openPanel();
        expect(await within(panel).findByText('Nothing new.')).toBeInTheDocument();
    });
});
