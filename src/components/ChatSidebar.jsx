import { useState } from 'react';
import {
    Trash2, RefreshCw, Volume2, VolumeX, MessageSquare, Bot, Brain,
    Plus, Pencil, ChevronDown, ChevronRight, ScrollText, Check, X,
} from 'lucide-react';

export default function ChatSidebar({
    theme,
    darkMode,
    effectiveIsMobile,
    sidebarOpen,
    // Ollama config
    ollamaHost, setOllamaHost,
    ollamaPort, setOllamaPort,
    selectedModel, setSelectedModel,
    availableModels,
    reachable,
    refreshModels,
    // TTS preferences
    chatTtsMode, setChatTtsMode,
    chatAutoTts, setChatAutoTts,
    // Model options
    enableThinking, setEnableThinking,
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
}) {
    const [renamingId, setRenamingId] = useState(null);
    const [draftTitle, setDraftTitle] = useState('');
    const [logOpen, setLogOpen] = useState(false);
    const activeMeta = sessions.find(s => s.id === activeSessionId);
    const userMessageCount = messages.filter(m => m.role === 'user').length;
    return (
        <aside className={`
            ${effectiveIsMobile
                ? `fixed inset-y-0 left-0 z-40 w-80 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`
                : `relative ${sidebarOpen ? 'w-80' : 'w-0'} overflow-hidden`}
            ${theme.bgSecondary} border-r ${theme.border} flex flex-col shadow-xl transition-all duration-300 ease-in-out
            ${effectiveIsMobile && sidebarOpen ? 'pt-16' : ''}
        `}>
            <div className={`${effectiveIsMobile ? '' : 'w-80'} flex flex-col h-full`}>
                {/* HEADER */}
                <div className={`px-4 py-3 border-b ${theme.borderSecondary} flex items-center gap-2`}>
                    <MessageSquare size={14} className={theme.textMuted} />
                    <h3 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Chat</h3>
                </div>

                {/* SETTINGS */}
                <div className={`px-4 py-4 space-y-4 border-b ${theme.borderSecondary} overflow-y-auto`}>
                    {/* Ollama host/port */}
                    <div className="space-y-2">
                        <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>OLLAMA SERVER</span>
                        <div className="flex items-center gap-2">
                            <span className={`text-[10px] font-bold ${theme.textMuted} w-10 shrink-0`}>Host</span>
                            <input
                                type="text"
                                value={ollamaHost}
                                onChange={(e) => setOllamaHost(e.target.value)}
                                placeholder="localhost"
                                className={`flex-1 text-xs font-bold p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors min-w-0`}
                            />
                        </div>
                        <div className="flex items-center gap-2">
                            <span className={`text-[10px] font-bold ${theme.textMuted} w-10 shrink-0`}>Port</span>
                            <input
                                type="text"
                                value={ollamaPort}
                                onChange={(e) => setOllamaPort(e.target.value)}
                                placeholder="11434"
                                className={`flex-1 text-xs font-bold p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors min-w-0`}
                            />
                        </div>
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
                    </div>

                    {/* Model picker */}
                    <div className="space-y-1">
                        <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>MODEL</span>
                        <select
                            value={selectedModel}
                            onChange={(e) => setSelectedModel(e.target.value)}
                            disabled={availableModels.length === 0}
                            className={`w-full text-xs font-bold p-2.5 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors ${availableModels.length === 0 ? 'opacity-60' : ''}`}
                        >
                            {availableModels.length === 0 ? (
                                <option value="">No models found</option>
                            ) : (
                                <>
                                    {!selectedModel && <option value="">Select a model…</option>}
                                    {availableModels.map(name => (
                                        <option key={name} value={name}>{name}</option>
                                    ))}
                                </>
                            )}
                        </select>
                        {selectedModel && (
                            <p className={`text-[9px] ${theme.textMuted} px-1 flex items-center gap-1`}>
                                <Bot size={10} /> Active: {selectedModel}
                            </p>
                        )}
                    </div>

                    {/* TTS mode toggle */}
                    <div className="space-y-2">
                        <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>READ-ALOUD MODE</span>
                        <div className={`flex p-1 rounded-lg border ${theme.border} ${theme.bgTertiary}`}>
                            <button
                                onClick={() => setChatTtsMode('streaming')}
                                className={`flex-1 text-[10px] font-bold py-1.5 rounded-md transition-colors ${chatTtsMode === 'streaming' ? 'bg-blue-600 text-white shadow' : `${theme.textSecondary} hover:text-blue-500`}`}
                            >
                                Streaming
                            </button>
                            <button
                                onClick={() => setChatTtsMode('after-complete')}
                                className={`flex-1 text-[10px] font-bold py-1.5 rounded-md transition-colors ${chatTtsMode === 'after-complete' ? 'bg-blue-600 text-white shadow' : `${theme.textSecondary} hover:text-blue-500`}`}
                            >
                                After complete
                            </button>
                        </div>
                        <p className={`text-[9px] ${theme.textMuted} px-1`}>
                            {chatTtsMode === 'streaming'
                                ? 'Reads each sentence as it streams in.'
                                : 'Waits for the full reply before reading.'}
                        </p>
                        <button
                            onClick={() => setChatAutoTts(!chatAutoTts)}
                            className={`w-full flex items-center justify-between p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.hover} text-xs font-bold transition-colors`}
                        >
                            <span className={theme.textSecondary}>Auto read-aloud</span>
                            {chatAutoTts ? (
                                <Volume2 size={14} className="text-blue-500" />
                            ) : (
                                <VolumeX size={14} className={theme.textMuted} />
                            )}
                        </button>
                    </div>

                    {/* Model options */}
                    <div className="space-y-2">
                        <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>MODEL OPTIONS</span>
                        <button
                            onClick={() => setEnableThinking(!enableThinking)}
                            className={`w-full flex items-center justify-between p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.hover} text-xs font-bold transition-colors`}
                        >
                            <span className={`flex items-center gap-2 ${theme.textSecondary}`}>
                                <Brain size={14} className={enableThinking ? 'text-blue-500' : theme.textMuted} />
                                Enable thinking
                            </span>
                            <span className={`text-[10px] font-bold ${enableThinking ? 'text-blue-500' : theme.textMuted}`}>
                                {enableThinking ? 'ON' : 'OFF'}
                            </span>
                        </button>
                        <p className={`text-[9px] ${theme.textMuted} px-1`}>
                            Asks the model to expose its reasoning. Has no effect on models that don't support it.
                        </p>
                    </div>
                </div>

                {/* SESSIONS + ACTIVE SUMMARY + LOG */}
                <div className="flex-1 flex flex-col overflow-hidden">
                    {/* Sessions list */}
                    <div className={`px-4 py-3 border-b ${theme.borderSecondary}`}>
                        <div className="flex items-center justify-between mb-2">
                            <h4 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Sessions</h4>
                            <button
                                onClick={newSession}
                                className={`text-[10px] font-bold px-2 py-1 rounded-lg ${theme.hover} ${theme.textSecondary} hover:text-blue-500 transition-colors flex items-center gap-1`}
                                title="Start a new chat"
                            >
                                <Plus size={10} /> New
                            </button>
                        </div>
                        <div className="space-y-1 max-h-48 overflow-y-auto custom-scrollbar pr-1">
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
                                                        <p className={`font-medium truncate ${isActive ? theme.text : theme.textSecondary}`}>
                                                            {s.title || 'Untitled'}
                                                        </p>
                                                        <p className={`text-[9px] ${theme.textMuted}`}>
                                                            {s.messageCount} msg · {formatTimeAgo(s.updatedAt)}
                                                        </p>
                                                    </button>
                                                    <button
                                                        onClick={(e) => { e.stopPropagation(); setRenamingId(s.id); setDraftTitle(s.title || ''); }}
                                                        className={`p-1 rounded opacity-0 group-hover:opacity-100 ${theme.hover} ${theme.textMuted} hover:text-blue-500 transition-opacity`}
                                                        title="Rename"
                                                    >
                                                        <Pencil size={10} />
                                                    </button>
                                                    <button
                                                        onClick={(e) => { e.stopPropagation(); if (confirm(`Delete "${s.title || 'this session'}"?`)) deleteSession(s.id); }}
                                                        className={`p-1 rounded opacity-0 group-hover:opacity-100 ${theme.hover} ${theme.textMuted} hover:text-red-500 transition-opacity`}
                                                        title="Delete session"
                                                    >
                                                        <Trash2 size={10} />
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    );
                                })
                            )}
                        </div>
                    </div>

                    {/* Active session summary + Clear (= start fresh, doesn't delete saved session) */}
                    <div className={`px-4 py-3 border-b ${theme.borderSecondary}`}>
                        <div className="flex items-center justify-between mb-1">
                            <h4 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Conversation</h4>
                            {messages.length > 0 && (
                                <button
                                    onClick={clearHistory}
                                    className={`text-[10px] font-bold px-2 py-1 rounded-lg ${theme.hover} ${theme.textMuted} hover:text-red-500 transition-colors flex items-center gap-1`}
                                    title="Start a fresh chat (saved session preserved)"
                                >
                                    <Plus size={10} /> New chat
                                </button>
                            )}
                        </div>
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
                    </div>

                    {/* Session event log (collapsible) */}
                    <div className="flex-1 flex flex-col overflow-hidden">
                        <button
                            onClick={() => setLogOpen((o) => !o)}
                            className={`w-full flex items-center justify-between px-4 py-2 ${theme.hover} transition-colors`}
                        >
                            <div className="flex items-center gap-2">
                                {logOpen ? <ChevronDown size={12} className={theme.textMuted} /> : <ChevronRight size={12} className={theme.textMuted} />}
                                <ScrollText size={12} className={theme.textMuted} />
                                <h4 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Session log</h4>
                            </div>
                            <span className={`text-[10px] ${theme.textMuted}`}>{events.length}</span>
                        </button>
                        {logOpen && (
                            <div className="px-4 pb-3 flex-1 overflow-y-auto custom-scrollbar text-[11px] font-mono leading-relaxed">
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
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </aside>
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
