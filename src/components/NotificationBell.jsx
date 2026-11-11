import { useRef, useState } from 'react';
import { AlertTriangle, Bell, CheckCircle2, ChevronDown, ChevronRight, Info } from 'lucide-react';
import { useMenuDismiss } from '../hooks/useMenuDismiss';
import { fetchInbox, markAllRead, markRead } from '../lib/notifications';
import { timeAgo } from '../utils/timeAgo';

const PAGE = 30;
const WALK = 100;   // the route's largest page, for Show more's walk from the top

/**
 * The header's bell and its inbox (spec docs/superpowers/specs/2026-11-06-notifications-design.md). An inbox, never
 * a popup: nothing opens by itself. The badge counts unread rows (a group once) and turns to the warning colour when
 * one needs action. Opening the list marks nothing read; clicking a notice does (and opens what it points to), and
 * clicking a group marks every member read and expands it in place. The rows come in the server's order: unread
 * before read, needing action first among unread, newest first.
 *
 * `count`/`action` come from useNotifications; `onRefresh` asks it again after something was marked read;
 * `onOpenTarget(target)` opens `{doc, name}` or `{admin: section}`.
 */
export default function NotificationBell({ theme, apiHost, apiPort, count = 0, action = 0, onRefresh, onOpenTarget }) {
    const [open, setOpen] = useState(false);
    const [rows, setRows] = useState(null);      // null while loading
    const [hasMore, setHasMore] = useState(false);
    const [expanded, setExpanded] = useState(() => new Set());
    const wrapRef = useRef(null);
    const openerRef = useRef(null);
    const close = () => setOpen(false);
    useMenuDismiss({ open, onClose: close, wrapRef, openerRef });

    // Each opening of the panel is a generation: an answer for an earlier one is dropped.
    const generation = useRef(0);

    const load = async () => {
        const mine = generation.current;
        const page = await fetchInbox(apiHost, apiPort, { offset: 0, limit: PAGE });
        if (mine !== generation.current) return;
        setRows(page ? page.rows : []);
        setHasMore(!!page && page.rows.length < page.total);
    };

    // The server's list moves while it is open: a row that was read sinks below the unread ones, and a new
    // notice arrives on top. An offset into it would skip a row or show one twice. So Show more reads from the
    // top again and adds the rows that share no notice with one already shown, in the server's order.
    const showMore = async () => {
        const mine = generation.current;
        const seen = new Set((rows || []).flatMap((r) => r.ids));
        const fresh = [];
        let offset = 0;
        let total;
        for (;;) {
            const page = await fetchInbox(apiHost, apiPort, { offset, limit: WALK });
            if (mine !== generation.current || !page) return;
            total = page.total;
            for (const row of page.rows) {
                if (row.ids.some((id) => seen.has(id))) continue;
                row.ids.forEach((id) => seen.add(id));
                fresh.push(row);
            }
            offset += page.rows.length;
            if (fresh.length > PAGE || offset >= total || page.rows.length === 0) break;
        }
        setRows((prev) => [...(prev || []), ...fresh.slice(0, PAGE)]);
        setHasMore(fresh.length > PAGE || offset < total);
    };

    const toggle = () => {
        generation.current += 1;
        if (open) {
            close();
            return;
        }
        setRows(null);
        setHasMore(false);
        setExpanded(new Set());
        setOpen(true);
        load();
    };

    // Mark a row's members read here at once, so the list doesn't wait for the server to look right.
    const readLocally = (row) => setRows((prev) => prev.map((r) => (r === row
        ? { ...r, unread: false, items: r.items.map((i) => ({ ...i, unread: false })) }
        : r)));

    const openTarget = (target) => {
        close();
        if (target) onOpenTarget?.(target);
    };

    const clickSingle = async (row) => {
        if (row.unread) {
            readLocally(row);
            await markRead(apiHost, apiPort, row.ids);
            onRefresh?.();
        }
        openTarget(row.target);
    };

    const rowKey = (row) => row.ids[0];

    const clickGroup = async (row) => {
        const key = rowKey(row);
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
        if (row.unread) {
            readLocally(row);
            await markRead(apiHost, apiPort, row.ids);
            onRefresh?.();
        }
    };

    const readAll = async () => {
        await markAllRead(apiHost, apiPort);
        onRefresh?.();
        await load();
    };

    const anyUnread = count > 0 || (rows || []).some((r) => r.unread);
    const label = count > 0
        ? `Notifications, ${count} unread${action > 0 ? `, ${action} needs action` : ''}`
        : 'Notifications';

    return (
        <div ref={wrapRef} data-own-keys className="relative">
            <button
                ref={openerRef}
                onClick={toggle}
                className={`relative p-2 ${theme.bgTertiary} rounded-xl ${theme.hover} transition-all ${theme.textSecondary} hover:text-blue-500 ${open ? 'text-blue-500' : ''}`}
                aria-haspopup="dialog"
                aria-expanded={open}
                aria-label={label}
                title="Notifications"
            >
                <Bell size={20} />
                {count > 0 && (
                    <span
                        data-badge
                        data-level={action > 0 ? 'action' : 'info'}
                        aria-hidden="true"
                        className={`absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold leading-[18px] text-center text-white ${action > 0 ? 'bg-amber-600' : 'bg-blue-600'}`}
                    >
                        {count > 99 ? '99+' : count}
                    </span>
                )}
            </button>
            {open && (
                <div
                    role="dialog"
                    aria-label="Notifications"
                    className={`fixed inset-x-0 top-14 sm:absolute sm:inset-x-auto sm:top-auto sm:right-0 sm:mt-2 sm:w-96 max-h-[70vh] overflow-y-auto rounded-none sm:rounded-xl shadow-2xl border ${theme.border} ${theme.bgSecondary} z-40`}
                >
                    <div className={`sticky top-0 flex items-center justify-between gap-2 px-4 py-2 border-b ${theme.border} ${theme.bgSecondary}`}>
                        <span className={`text-sm font-bold ${theme.text}`}>Notifications</span>
                        <button
                            onClick={readAll}
                            disabled={!anyUnread}
                            className={`text-xs font-semibold px-2 min-h-[44px] md:min-h-0 md:py-1 rounded-lg ${theme.hover} text-blue-500 disabled:opacity-40 disabled:cursor-not-allowed`}
                        >
                            Mark all as read
                        </button>
                    </div>
                    {rows === null && <p className={`px-4 py-6 text-sm ${theme.textMuted}`}>Loading…</p>}
                    {rows !== null && rows.length === 0 && (
                        <p className={`px-4 py-6 text-sm ${theme.textMuted}`}>Nothing new.</p>
                    )}
                    {rows !== null && rows.length > 0 && (
                        <ul>
                            {rows.map((row) => (
                                <NoticeRow
                                    key={rowKey(row)}
                                    row={row}
                                    theme={theme}
                                    expanded={expanded.has(rowKey(row))}
                                    onClick={() => (row.count > 1 ? clickGroup(row) : clickSingle(row))}
                                    onOpenMember={(item) => openTarget(item.target)}
                                />
                            ))}
                        </ul>
                    )}
                    {rows !== null && hasMore && (
                        <button
                            onClick={showMore}
                            className={`w-full px-4 min-h-[44px] md:min-h-0 md:py-2 text-sm font-semibold text-blue-500 ${theme.hover} border-t ${theme.border}`}
                        >
                            Show more
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

function LevelIcon({ row }) {
    if (row.needs_action) return <AlertTriangle size={16} className="shrink-0 mt-0.5 text-amber-600" aria-hidden="true" />;
    if (row.level === 'action') return <CheckCircle2 size={16} className="shrink-0 mt-0.5 text-green-600" aria-hidden="true" />;
    return <Info size={16} className="shrink-0 mt-0.5 text-blue-500" aria-hidden="true" />;
}

function NoticeRow({ row, theme, expanded, onClick, onOpenMember }) {
    const isGroup = row.count > 1;
    const weight = row.unread ? `font-semibold ${theme.text}` : `font-normal ${theme.textSecondary}`;
    let tag = null;
    if (isGroup && row.resolved > 0) tag = `${row.resolved} resolved`;
    else if (!isGroup && row.resolved > 0) tag = 'Resolved';
    return (
        <li data-unread={row.unread ? 'true' : 'false'} className={`border-b last:border-b-0 ${theme.border}`}>
            <button
                onClick={onClick}
                aria-expanded={isGroup ? expanded : undefined}
                className={`w-full flex items-start gap-3 px-4 py-2.5 min-h-[44px] text-left ${theme.hover} transition-colors`}
            >
                <LevelIcon row={row} />
                <span className="min-w-0 flex-1">
                    <span className={`block text-sm ${weight}`}>{row.title}</span>
                    {row.body && <span className={`block text-xs truncate ${theme.textMuted}`}>{row.body}</span>}
                    <span className={`block text-[11px] ${theme.textMuted}`}>
                        {timeAgo(row.at)}
                        {tag && <span className="ml-2 px-1.5 rounded bg-green-600/15 text-green-700 dark:text-green-400">{tag}</span>}
                    </span>
                </span>
                {row.unread && <span className="shrink-0 mt-1.5 w-2 h-2 rounded-full bg-blue-500" aria-label="unread" />}
                {isGroup && (expanded
                    ? <ChevronDown size={16} className={`shrink-0 mt-0.5 ${theme.textMuted}`} aria-hidden="true" />
                    : <ChevronRight size={16} className={`shrink-0 mt-0.5 ${theme.textMuted}`} aria-hidden="true" />)}
            </button>
            {isGroup && expanded && (
                <ul className="pb-1">
                    {row.items.map((item) => (
                        <li key={item.id}>
                            <button
                                onClick={() => onOpenMember(item)}
                                className={`w-full flex items-start gap-2 pl-11 pr-4 py-1.5 min-h-[44px] md:min-h-0 text-left ${theme.hover}`}
                            >
                                <span className="min-w-0 flex-1">
                                    <span className={`block text-xs ${theme.text}`}>{item.title}</span>
                                    {item.body && <span className={`block text-[11px] truncate ${theme.textMuted}`}>{item.body}</span>}
                                    <span className={`block text-[11px] ${theme.textMuted}`}>
                                        {timeAgo(item.at)}
                                        {item.resolved && <span className="ml-2 text-green-700 dark:text-green-400">Resolved</span>}
                                    </span>
                                </span>
                            </button>
                        </li>
                    ))}
                    {row.more > 0 && <li className={`pl-11 pr-4 py-1.5 text-[11px] ${theme.textMuted}`}>and {row.more} more</li>}
                </ul>
            )}
        </li>
    );
}
