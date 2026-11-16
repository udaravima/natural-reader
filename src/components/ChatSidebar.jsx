import { useEffect, useMemo, useState } from 'react';
import {
    Trash2, RefreshCw, MessageSquare, Bot,
    Plus, Pencil, ChevronDown, ChevronRight, ScrollText, Check, X, Folder, Sparkles,
} from 'lucide-react';
import { modelOptions } from '../lib/modelIds';
import { SearchableSelect } from './SearchableList';

// A chat row's buttons (Rename, Generate title, Delete). With a mouse they show on hover; a screen with no hover
// could never show them (Tailwind applies group-hover only where hover exists), so there they always show, and a
// coarse pointer gets a 32 px target and a larger icon. Keyboard focus shows them too.
const ROW_ACTION = 'p-1 rounded shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 '
    + '[@media(hover:none)]:opacity-100 pointer-coarse:p-[9px] transition-opacity';
const ROW_ICON = 'pointer-coarse:size-3.5';
// Always-shown buttons leave the title less room, and a touch screen has no tooltip to read the rest: two lines.
const ROW_TITLE = '[@media(hover:none)]:whitespace-normal [@media(hover:none)]:line-clamp-2 [@media(hover:none)]:break-words';

export default function ChatSidebar({
    theme,
    darkMode,
    effectiveIsMobile,
    sidebarOpen,
    selectedModel, setSelectedModel,
    availableModels,
    inferenceBudget,
    reachable,
    refreshModels,
    // Chat state
    messages,
    clearHistory,
    // Sessions + log
    sessions,
    activeSessionId,
    events,
    newSession,
    switchToSession,
    deleteSession,
    renameSession,
    helperOn = false,
    refreshHelper,
    generateTitle,
    // The assistant's profile for the open chat (profile library spec): the list, what the chat uses, a pick.
    profiles = [],
    profileId = '',
    setProfile,
    currentProfile = null,
    removedProfile = null,
}) {
    const modelChoices = useMemo(() => modelOptions(availableModels, selectedModel, { prompt: 'Select a model…' }),
        [availableModels, selectedModel]);
    // The admin's profiles that are on, and the chat's own if it was turned off since (it still applies to this chat).
    const profileChoices = useMemo(() => {
        const listed = profiles.map((p) => ({ id: p.id, label: p.isDefault ? `${p.name} (default)` : p.name }));
        return currentProfile?.id && !profiles.some((p) => p.id === currentProfile.id)
            ? [...listed, { id: currentProfile.id, label: `${currentProfile.name} (turned off)` }]
            : listed;
    }, [profiles, currentProfile]);
    const pickedDescription = profiles.find((p) => p.id === profileId)?.description || '';
    const [renamingId, setRenamingId] = useState(null);
    const [draftTitle, setDraftTitle] = useState('');
    const activeMeta = sessions.find(s => s.id === activeSessionId);
    // The helper can be turned on or off while this page is open: ask again each time the chat sidebar opens.
    useEffect(() => { refreshHelper?.(); }, [refreshHelper]);
    const userMessageCount = messages.filter(m => m.role === 'user').length;
    return (
        <aside className={`
            ${effectiveIsMobile
                ? `fixed inset-y-0 left-0 z-40 w-80 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`
                : `relative ${sidebarOpen ? 'w-80' : 'w-0'} overflow-hidden`}
            ${theme.bgSecondary} border-r ${theme.border} flex flex-col shadow-xl transition-all duration-300 ease-in-out
            ${effectiveIsMobile && sidebarOpen ? 'pt-16' : ''}
        `}>
            <div className={`${effectiveIsMobile ? '' : 'w-80'} flex flex-col h-full overflow-hidden`}>
                {/* HEADER (sticky at top) */}
                <div className={`px-4 py-3 border-b ${theme.borderSecondary} flex items-center gap-2 shrink-0`}>
                    <MessageSquare size={14} className={theme.textMuted} />
                    <h3 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Chat</h3>
                </div>

                {/* All sections live inside one outer scroll container so they stack
                    naturally and the user can scroll the whole sidebar when many are open. */}
                <div className="flex-1 overflow-y-auto custom-scrollbar">

                {/* MODEL — pick the active model; full chat/inference settings
                    now live on the Settings page. */}
                <Section
                    theme={theme}
                    title="Model"
                    defaultOpen
                    bodyClassName="px-4 py-4 space-y-4 max-h-[60vh] overflow-y-auto custom-scrollbar"
                >
                    <div className="flex items-center justify-between gap-2 mt-1">
                            <span className={`text-[10px] ${reachable === null ? theme.textMuted : reachable ? 'text-green-500' : 'text-red-400'}`}>
                                {reachable === null ? '⏳ Checking...' : reachable ? '✓ Connected' : '✗ Unreachable'}
                            </span>
                            <button
                                onClick={refreshModels}
                                className={`text-[10px] font-bold px-2 py-1 rounded-lg ${theme.hover} ${theme.textSecondary} hover:text-blue-500 transition-colors flex items-center gap-1`}
                            >
                                <RefreshCw size={10} /> Refresh
                            </button>
                        </div>

                    {/* Model picker */}
                    <div className="space-y-1">
                        <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>MODEL</span>
                        <SearchableSelect
                            theme={theme}
                            findLabel="Find a model"
                            aria-label="Model"
                            value={selectedModel}
                            onChange={setSelectedModel}
                            options={modelChoices}
                            disabled={availableModels.length === 0}
                            className={`w-full text-xs font-bold p-2.5 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors ${availableModels.length === 0 ? 'opacity-60' : ''}`}
                        />
                        {selectedModel && (
                            <p className={`text-[9px] ${theme.textMuted} px-1 flex items-center gap-1`}>
                                <Bot size={10} /> Active: {selectedModel}
                            </p>
                        )}
                        {inferenceBudget?.remaining_tokens != null && (
                            <p className={`text-[9px] px-1 ${inferenceBudget.remaining_tokens === 0 ? 'text-red-400 font-bold' : theme.textMuted}`}>
                                {inferenceBudget.remaining_tokens.toLocaleString()} tokens left today
                            </p>
                        )}
                    </div>

                    {/* Profile picker: who the assistant is in this chat. Only when an admin offers a choice. */}
                    {profileChoices.length > 1 && (
                        <div className="space-y-1">
                            <label htmlFor="chat-profile" className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>PROFILE</label>
                            <select
                                id="chat-profile"
                                aria-label="Profile"
                                value={profileId}
                                onChange={(e) => setProfile?.(e.target.value)}
                                className={`w-full text-xs font-bold p-2.5 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none`}
                            >
                                {profileChoices.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                            </select>
                            {pickedDescription && (
                                <p className={`text-[9px] ${theme.textMuted} px-1`}>{pickedDescription}</p>
                            )}
                        </div>
                    )}
                    {removedProfile && (
                        <p className={`text-[10px] ${theme.textMuted} px-1`}>
                            The profile this chat used, {removedProfile}, was removed.
                        </p>
                    )}

                </Section>

                {/* SESSIONS */}
                <Section
                    theme={theme}
                    title="Sessions"
                    count={sessions.length}
                    defaultOpen={true}
                    headerAction={
                        <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => { e.stopPropagation(); newSession(); }}
                            onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); newSession(); } }}
                            className={`text-[10px] font-bold px-2 py-1 rounded-lg ${theme.hover} ${theme.textSecondary} hover:text-blue-500 transition-colors flex items-center gap-1 cursor-pointer`}
                            title="Start a new chat"
                        >
                            <Plus size={10} /> New
                        </span>
                    }
                    bodyClassName="px-4 py-2 max-h-[40vh] overflow-y-auto custom-scrollbar"
                >
                        <div className="space-y-1">
                            {sessions.length === 0 ? (
                                <p className={`text-xs ${theme.textMuted} italic`}>No saved sessions yet.</p>
                            ) : (
                                sessions.map((s) => {
                                    const isActive = s.id === activeSessionId;
                                    const isRenaming = renamingId === s.id;
                                    return (
                                        <div
                                            key={s.id}
                                            className={`group flex items-center gap-1 p-2 rounded-lg text-xs transition-colors ${isActive
                                                ? darkMode ? 'bg-blue-600/20 border border-blue-500/40' : 'bg-blue-100 border border-blue-300'
                                                : `${theme.hover} border border-transparent`}`}
                                        >
                                            {s.source === 'local' && !isRenaming && (
                                                <span
                                                    className={`shrink-0 px-1 py-px rounded text-[8px] font-black uppercase tracking-wider ${darkMode ? 'bg-amber-500/20 text-amber-300' : 'bg-amber-100 text-amber-700'}`}
                                                    title="Stored locally in your browser (legacy). Editing forks to a new server session."
                                                >
                                                    Local
                                                </span>
                                            )}
                                            {isRenaming ? (
                                                <>
                                                    <input
                                                        type="text"
                                                        value={draftTitle}
                                                        onChange={(e) => setDraftTitle(e.target.value)}
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Enter') {
                                                                renameSession(s.id, draftTitle);
                                                                setRenamingId(null);
                                                            } else if (e.key === 'Escape') {
                                                                setRenamingId(null);
                                                            }
                                                        }}
                                                        autoFocus
                                                        className={`flex-1 min-w-0 px-1.5 py-0.5 rounded border ${theme.border} ${theme.bgSecondary} ${theme.text} text-xs outline-none focus:ring-1 focus:ring-blue-500`}
                                                    />
                                                    <button
                                                        onClick={() => { renameSession(s.id, draftTitle); setRenamingId(null); }}
                                                        className={`p-1 rounded ${theme.hover} text-blue-500`}
                                                        title="Save"
                                                    >
                                                        <Check size={11} />
                                                    </button>
                                                    <button
                                                        onClick={() => setRenamingId(null)}
                                                        className={`p-1 rounded ${theme.hover} ${theme.textMuted}`}
                                                        title="Cancel"
                                                    >
                                                        <X size={11} />
                                                    </button>
                                                </>
                                            ) : (
                                                <>
                                                    <button
                                                        onClick={() => switchToSession(s.id)}
                                                        className="flex-1 min-w-0 text-left"
                                                        title={`${s.messageCount} message${s.messageCount === 1 ? '' : 's'} · ${new Date(s.updatedAt).toLocaleString()}`}
                                                    >
                                                        <p className={`font-medium truncate ${ROW_TITLE} ${isActive ? theme.text : theme.textSecondary}`}>
                                                            {s.title || 'Untitled'}
                                                        </p>
                                                        <p className={`text-[9px] ${theme.textMuted}`}>
                                                            {s.messageCount} msg · {formatTimeAgo(s.updatedAt)}
                                                        </p>
                                                        {s.scopeProject && (
                                                            // A project chat. The name is null when the reader can no longer see the project.
                                                            <p className="flex items-center gap-1 min-w-0 text-[9px] text-blue-500">
                                                                <Folder size={9} className="shrink-0" aria-hidden="true" />
                                                                <span className="truncate">{s.scopeProject.name || 'Project unavailable'}</span>
                                                            </p>
                                                        )}
                                                    </button>
                                                    <button
                                                        onClick={(e) => { e.stopPropagation(); setRenamingId(s.id); setDraftTitle(s.title || ''); }}
                                                        aria-label={`Rename ${s.title || 'Untitled'}`}
                                                        className={`${ROW_ACTION} ${theme.hover} ${theme.textMuted} hover:text-blue-500`}
                                                        title="Rename"
                                                    >
                                                        <Pencil size={10} className={ROW_ICON} aria-hidden="true" />
                                                    </button>
                                                    {helperOn && generateTitle && s.source === 'pg' && (
                                                        // Generate title (helper spec §6): the helper writes it from the conversation so far.
                                                        <button
                                                            onClick={(e) => { e.stopPropagation(); generateTitle(s.id); }}
                                                            disabled={Boolean(s.titlePending)}
                                                            aria-label={`Generate title for ${s.title || 'Untitled'}`}
                                                            className={`${ROW_ACTION} disabled:opacity-50! ${theme.hover} ${theme.textMuted} hover:text-blue-500`}
                                                            title={s.titlePending ? 'Writing a title…' : 'Generate title'}
                                                        >
                                                            <Sparkles size={10} className={ROW_ICON} aria-hidden="true" />
                                                        </button>
                                                    )}
                                                    <button
                                                        onClick={(e) => { e.stopPropagation(); if (confirm(`Delete "${s.title || 'this session'}"?`)) deleteSession(s.id); }}
                                                        aria-label={`Delete ${s.title || 'Untitled'}`}
                                                        className={`${ROW_ACTION} ${theme.hover} ${theme.textMuted} hover:text-red-500`}
                                                        title="Delete session"
                                                    >
                                                        <Trash2 size={10} className={ROW_ICON} aria-hidden="true" />
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    );
                                })
                            )}
                        </div>
                </Section>

                {/* CONVERSATION (active session info) */}
                <Section
                    theme={theme}
                    title="Conversation"
                    defaultOpen={true}
                    headerAction={messages.length > 0 ? (
                        <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => { e.stopPropagation(); clearHistory(); }}
                            onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); clearHistory(); } }}
                            className={`text-[10px] font-bold px-2 py-1 rounded-lg ${theme.hover} ${theme.textMuted} hover:text-red-500 transition-colors flex items-center gap-1 cursor-pointer`}
                            title="Start a fresh chat (saved session preserved)"
                        >
                            <Plus size={10} /> New chat
                        </span>
                    ) : null}
                    bodyClassName="px-4 py-3 max-h-[30vh] overflow-y-auto"
                >
                    {messages.length === 0 ? (
                        <p className={`text-xs ${theme.textMuted} italic`}>No messages yet.</p>
                    ) : (
                        <div className={`text-xs ${theme.textSecondary} space-y-0.5`}>
                            <p className="truncate" title={activeMeta?.title}>
                                {activeMeta?.title || 'New chat (unsaved)'}
                            </p>
                            <p className={`text-[10px] ${theme.textMuted}`}>
                                {messages.length} message{messages.length === 1 ? '' : 's'} · {userMessageCount} from you
                            </p>
                        </div>
                    )}
                </Section>

                {/* SESSION LOG */}
                <Section
                    theme={theme}
                    title="Session log"
                    icon={<ScrollText size={12} className={theme.textMuted} />}
                    count={events.length}
                    defaultOpen={false}
                    bodyClassName="px-4 py-3 max-h-[40vh] overflow-y-auto custom-scrollbar text-[11px] font-mono leading-relaxed"
                >
                    {events.length === 0 ? (
                        <p className={`${theme.textMuted} italic`}>No events yet.</p>
                    ) : (
                        <ul className="space-y-1">
                            {events.map((e, i) => (
                                <li key={i} className={`flex gap-2 ${eventKindColor(e.kind, theme)}`}>
                                    <span className={`${theme.textMuted} shrink-0`}>{formatTime(e.ts)}</span>
                                    <span className="font-bold uppercase shrink-0">{e.kind}</span>
                                    <span className={`${theme.textSecondary} break-words min-w-0`}>{e.message}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </Section>

                </div>
            </div>
        </aside>
    );
}

// One labelled dropdown in the Inference block. Discrete options rather than a
// slider or free text: a num_ctx change forces Ollama to reload the model, so a
// value that changed on every keystroke would thrash the runner.
// Reusable collapsible section for the chat sidebar.
// Header is a button; body is conditionally rendered with its own scroll container.
function Section({ theme, title, icon, count, defaultOpen = true, headerAction, bodyClassName = '', children }) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className={`border-b ${theme.borderSecondary} flex flex-col shrink-0`}>
            <div
                onClick={() => setOpen((o) => !o)}
                className={`w-full flex items-center justify-between gap-2 px-4 py-2.5 ${theme.hover} transition-colors cursor-pointer select-none`}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((o) => !o); } }}
            >
                <div className="flex items-center gap-2 min-w-0">
                    {open ? <ChevronDown size={12} className={theme.textMuted} /> : <ChevronRight size={12} className={theme.textMuted} />}
                    {icon}
                    <h4 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest truncate`}>{title}</h4>
                    {count !== undefined && (
                        <span className={`text-[10px] ${theme.textMuted}`}>{count}</span>
                    )}
                </div>
                {headerAction}
            </div>
            {open && (
                <div className={bodyClassName}>
                    {children}
                </div>
            )}
        </div>
    );
}

// ----- helpers -----
function formatTimeAgo(ts) {
    if (!ts) return '';
    const diff = Date.now() - ts;
    if (diff < 60_000) return 'just now';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return new Date(ts).toLocaleDateString();
}

function formatTime(ts) {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function eventKindColor(kind, theme) {
    switch (kind) {
        case 'error': return 'text-red-400';
        case 'aborted': return 'text-amber-400';
        case 'received': return theme.text;
        default: return theme.textSecondary;
    }
}
