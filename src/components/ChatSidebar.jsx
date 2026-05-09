import { Trash2, RefreshCw, Volume2, VolumeX, MessageSquare, Bot } from 'lucide-react';

export default function ChatSidebar({
    theme,
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
    // Chat state
    messages,
    clearHistory,
}) {
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
                </div>

                {/* CONVERSATION SUMMARY + CLEAR */}
                <div className="flex-1 px-4 py-3 overflow-y-auto custom-scrollbar">
                    <div className="flex items-center justify-between mb-2">
                        <h4 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Conversation</h4>
                        {messages.length > 0 && (
                            <button
                                onClick={clearHistory}
                                className={`text-[10px] font-bold px-2 py-1 rounded-lg ${theme.hover} ${theme.textMuted} hover:text-red-500 transition-colors flex items-center gap-1`}
                                title="Clear chat history"
                            >
                                <Trash2 size={10} /> Clear
                            </button>
                        )}
                    </div>
                    {messages.length === 0 ? (
                        <p className={`text-xs ${theme.textMuted} italic`}>No messages yet.</p>
                    ) : (
                        <p className={`text-xs ${theme.textSecondary}`}>
                            {messages.length} message{messages.length === 1 ? '' : 's'}
                            {' · '}
                            {messages.filter(m => m.role === 'user').length} from you
                        </p>
                    )}
                </div>
            </div>
        </aside>
    );
}
