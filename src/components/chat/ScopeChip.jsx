import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Check, ChevronDown, FileText, FileX, Folder, FolderOpen } from 'lucide-react';

const IDLE = { status: 'idle', projects: [] };

const labelOf = (scope, openDocName) => {
    if (scope.kind === 'project') return scope.project?.name ? `Project: ${scope.project.name}` : 'Project unavailable';
    if (scope.kind === 'document') return openDocName ? `This document: ${openDocName}` : 'This document';
    return 'No document';
};

const menuItems = (menu) => [...(menu?.querySelectorAll('[role="menuitem"]') ?? [])];

const TITLES = {
    none: "The assistant won't read a document in this chat.",
    document: 'The assistant can search and read this document.',
    project: "The assistant can search this project's documents.",
};

/**
 * What this chat is about (C2.1 spec §6): no document, the open document, or
 * a project. A menu button — Enter, Space or the arrow keys open it, the
 * arrows move, Escape closes it. Replaces the "Use this document" switch.
 *
 * `scope` is {kind: 'none'|'document'|'project', project?: {id, name}}; a
 * project whose `name` is null is one the reader can no longer see. Names are
 * rendered as text. `onChange` gets the same shape. "Choose a project…" calls
 * `loadProjects()` (a promise of [{id, name}]) only when chosen, not on mount.
 */
export default function ScopeChip({
    theme, scope, openDocName = null, openDocVersion = null, openDocProjects = [], onChange, loadProjects,
}) {
    const [open, setOpen] = useState(false);
    const [choose, setChoose] = useState(IDLE);   // the "Choose a project…" list: idle | loading | ready | empty | error
    const rootRef = useRef(null);
    const buttonRef = useRef(null);
    const menuRef = useRef(null);
    const loadSeq = useRef(0);                   // a load that finishes after a newer one, or after close, is dropped
    const menuId = useId();

    const close = useCallback((refocus) => {
        loadSeq.current += 1;
        setChoose(IDLE);
        setOpen(false);
        if (refocus) buttonRef.current?.focus();
    }, []);

    // Opened: focus the current scope's item, else the first.
    useEffect(() => {
        if (!open) return;
        const all = menuItems(menuRef.current);
        (all.find((el) => el.getAttribute('aria-current') === 'true') ?? all[0])?.focus();
    }, [open]);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => { if (!rootRef.current?.contains(e.target)) close(false); };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [open, close]);

    const pick = (next) => {
        close(true);
        onChange?.(next);
    };

    const chooseProject = () => {
        const seq = ++loadSeq.current;
        setChoose({ status: 'loading', projects: [] });
        new Promise((resolve) => resolve(loadProjects())).then(
            (list) => {
                if (loadSeq.current !== seq) return;
                const projects = Array.isArray(list) ? list : [];
                setChoose({ status: projects.length ? 'ready' : 'empty', projects });
            },
            () => { if (loadSeq.current === seq) setChoose({ status: 'error', projects: [] }); },
        );
    };

    const onKeyDown = (e) => {
        if (!open) {
            if (e.target === buttonRef.current && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                e.preventDefault();
                setOpen(true);
            }
            return;
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            close(true);
        } else if (e.key === 'Tab') {
            close(false);   // focus moves on by itself
        } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
            e.preventDefault();
            const all = menuItems(menuRef.current);
            if (all.length === 0) return;
            const at = all.indexOf(document.activeElement);
            const next = e.key === 'Home' ? 0
                : e.key === 'End' ? all.length - 1
                : e.key === 'ArrowDown' ? (at + 1) % all.length
                : (at < 0 ? all.length - 1 : (at - 1 + all.length) % all.length);
            all[next].focus();
        }
    };

    const isProject = (id) => scope.kind === 'project' && scope.project?.id === id;
    const projectItem = (p) => (
        <Item key={p.id} theme={theme} current={isProject(p.id)} icon={<Folder size={12} />}
            onSelect={() => pick({ kind: 'project', project: { id: p.id, name: p.name } })}>
            {p.name}
        </Item>
    );
    const accent = scope.kind === 'project' ? 'text-blue-500' : scope.kind === 'document' ? 'text-purple-500' : theme.textMuted;
    const Icon = scope.kind === 'project' ? Folder : scope.kind === 'document' ? FileText : FileX;
    const oldVersion = openDocVersion != null ? ` · v${openDocVersion} (old version)` : '';

    return (
        <div ref={rootRef} className="relative self-start max-w-full" onKeyDown={onKeyDown}>
            <button
                ref={buttonRef}
                type="button"
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                title={TITLES[scope.kind]}
                onClick={() => (open ? close(false) : setOpen(true))}
                className={`max-w-full flex items-center gap-2 px-3 py-1.5 rounded-full border text-[11px] ${theme.border} ${theme.bgTertiary} ${theme.text} outline-none focus-visible:ring-2 focus-visible:ring-blue-500`}
            >
                <Icon size={12} className={`shrink-0 ${accent}`} />
                <span className="truncate max-w-[16rem]">{labelOf(scope, openDocName)}</span>
                {scope.kind === 'document' && oldVersion && <span className={`shrink-0 ${theme.textMuted}`}>{oldVersion}</span>}
                <ChevronDown size={12} className={`shrink-0 ${theme.textMuted}`} />
            </button>
            {open && (
                <div className={`absolute left-0 bottom-full mb-1 z-30 w-72 max-w-[calc(100vw-2rem)] max-h-72 overflow-y-auto rounded-xl border shadow-lg p-1 ${theme.border} ${theme.bgSecondary}`}>
                    <div ref={menuRef} id={menuId} role="menu" aria-label="What this chat is about" className="flex flex-col">
                        <Item theme={theme} current={scope.kind === 'none'} icon={<FileX size={12} />} onSelect={() => pick({ kind: 'none' })}>
                            No document
                        </Item>
                        {openDocName && (
                            <Item theme={theme} current={scope.kind === 'document'} icon={<FileText size={12} />} onSelect={() => pick({ kind: 'document' })}>
                                {`This document: ${openDocName}${oldVersion}`}
                            </Item>
                        )}
                        {openDocProjects.map(projectItem)}
                        {loadProjects && (
                            <Item theme={theme} icon={<FolderOpen size={12} />} onSelect={chooseProject}>Choose a project…</Item>
                        )}
                        {choose.status === 'ready' && <div role="separator" className={`my-1 border-t ${theme.border}`} />}
                        {choose.status === 'ready' && choose.projects.map(projectItem)}
                    </div>
                    {choose.status === 'loading' && <Status theme={theme}>Loading projects…</Status>}
                    {choose.status === 'empty' && <Status theme={theme}>You're not in any project yet.</Status>}
                    {choose.status === 'error' && <Status theme={theme}>Couldn't load projects.</Status>}
                </div>
            )}
        </div>
    );
}

function Item({ theme, current = false, icon = null, onSelect, children }) {
    return (
        <button
            type="button"
            role="menuitem"
            tabIndex={-1}
            aria-current={current ? 'true' : undefined}
            onClick={onSelect}
            className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[11px] outline-none hover:bg-blue-500/10 focus:bg-blue-500/15 focus:ring-1 focus:ring-inset focus:ring-blue-500 ${theme.text} ${current ? 'font-bold' : ''}`}
        >
            {icon && <span aria-hidden="true" className={`shrink-0 ${theme.textMuted}`}>{icon}</span>}
            <span className="flex-1 min-w-0 truncate">{children}</span>
            {current && <Check size={12} aria-hidden="true" className="shrink-0 text-blue-500" />}
        </button>
    );
}

function Status({ theme, children }) {
    return <p role="status" className={`px-2.5 py-1.5 text-[11px] italic ${theme.textMuted}`}>{children}</p>;
}
