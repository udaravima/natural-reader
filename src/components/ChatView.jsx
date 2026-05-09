import { useEffect, useRef, useState } from 'react';
import { Send, Square, Bot, User, Loader2, MessageSquare } from 'lucide-react';

export default function ChatView({
    theme,
    darkMode,
    effectiveIsMobile,
    messages,
    isStreaming,
    selectedModel,
    reachable,
    sendMessage,
    stopStream,
}) {
    const [draft, setDraft] = useState('');
    const listRef = useRef(null);
    const textareaRef = useRef(null);

    // Auto-scroll to bottom on new message / streaming token.
    useEffect(() => {
        const el = listRef.current;
        if (!el) return;
        el.scrollTop = el.scrollHeight;
    }, [messages]);

    // Auto-grow the prompt textarea.
    useEffect(() => {
        const ta = textareaRef.current;
        if (!ta) return;
        ta.style.height = 'auto';
        ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
    }, [draft]);

    const canSend = draft.trim().length > 0 && !isStreaming && !!selectedModel && reachable !== false;

    const handleSend = () => {
        if (!canSend) return;
        sendMessage(draft);
        setDraft('');
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    return (
        <section className={`flex-1 flex flex-col overflow-hidden ${theme.viewportBg} transition-colors duration-300`}>
            {/* MESSAGE LIST */}
            <div
                ref={listRef}
                className={`flex-1 overflow-y-auto custom-scrollbar px-4 md:px-8 py-4 md:py-6 ${effectiveIsMobile ? 'pb-28' : ''}`}
            >
                {messages.length === 0 ? (
                    <EmptyState theme={theme} darkMode={darkMode} reachable={reachable} selectedModel={selectedModel} />
                ) : (
                    <div className="max-w-3xl mx-auto space-y-4">
                        {messages.map((m) => (
                            <MessageBubble key={m.id} message={m} theme={theme} darkMode={darkMode} />
                        ))}
                        {isStreaming && messages[messages.length - 1]?.role === 'assistant' && (
                            <div className="flex items-center gap-2 px-2 text-xs">
                                <Loader2 size={12} className="animate-spin text-blue-500" />
                                <span className={theme.textMuted}>Streaming…</span>
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* INPUT BAR */}
            <div className={`border-t ${theme.border} ${theme.bgSecondary} px-4 py-3 ${effectiveIsMobile ? 'pb-20' : ''}`}>
                <div className="max-w-3xl mx-auto flex items-end gap-2">
                    <textarea
                        ref={textareaRef}
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder={
                            reachable === false
                                ? 'Ollama unreachable — check host/port in sidebar.'
                                : !selectedModel
                                    ? 'Pick a model in the sidebar to start.'
                                    : 'Ask the model anything…  (Enter to send, Shift+Enter for newline)'
                        }
                        rows={1}
                        className={`flex-1 resize-none p-3 rounded-xl border ${theme.border} ${theme.bgTertiary} ${theme.text} text-sm leading-relaxed outline-none focus:ring-2 focus:ring-blue-500 transition-colors`}
                        style={{ maxHeight: '200px' }}
                    />
                    {isStreaming ? (
                        <button
                            onClick={stopStream}
                            className="p-3 rounded-xl bg-red-500 hover:bg-red-600 text-white shadow-md transition-colors"
                            title="Stop generation"
                        >
                            <Square size={18} fill="currentColor" />
                        </button>
                    ) : (
                        <button
                            onClick={handleSend}
                            disabled={!canSend}
                            className={`p-3 rounded-xl shadow-md transition-all ${canSend
                                ? 'bg-blue-600 hover:bg-blue-700 text-white'
                                : `${theme.bgTertiary} ${theme.textMuted} opacity-50 cursor-not-allowed`}`}
                            title="Send (Enter)"
                        >
                            <Send size={18} />
                        </button>
                    )}
                </div>
            </div>
        </section>
    );
}

function MessageBubble({ message, theme, darkMode }) {
    const isUser = message.role === 'user';
    const Icon = isUser ? User : Bot;
    return (
        <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white shrink-0 ${
                isUser
                    ? 'bg-gradient-to-br from-slate-600 to-slate-800'
                    : 'bg-gradient-to-br from-blue-500 to-cyan-600'
            }`}>
                <Icon size={16} />
            </div>
            <div className={`max-w-[85%] px-4 py-3 rounded-2xl ${isUser
                ? darkMode ? 'bg-blue-600/30 text-white rounded-tr-sm' : 'bg-blue-100 text-slate-900 rounded-tr-sm'
                : `${theme.bgSecondary} border ${theme.border} ${theme.text} rounded-tl-sm`
            }`}>
                <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                    {message.content || (message.role === 'assistant' ? '…' : '')}
                </p>
            </div>
        </div>
    );
}

function EmptyState({ theme, darkMode, reachable, selectedModel }) {
    return (
        <div className="h-full flex flex-col items-center justify-center text-center gap-4 px-6">
            <div className={`w-20 h-20 ${theme.bgTertiary} rounded-2xl flex items-center justify-center ${theme.textMuted} border-2 border-dashed ${theme.border}`}>
                <MessageSquare size={36} />
            </div>
            <div>
                <p className={`${theme.textSecondary} font-semibold mb-2 text-lg`}>Chat with a local model</p>
                <p className={`text-sm ${theme.textMuted} max-w-md`}>
                    Talk to Ollama and have the responses read aloud through Kokoro.
                </p>
            </div>
            <div className={`text-xs px-3 py-2 rounded-lg ${darkMode ? 'bg-slate-800/50' : 'bg-slate-100'} ${theme.textSecondary}`}>
                {reachable === false ? (
                    <>Ollama not reachable. Set host & port in the sidebar.</>
                ) : !selectedModel ? (
                    <>Pick a model in the sidebar to begin.</>
                ) : (
                    <>Model: <span className="font-bold">{selectedModel}</span></>
                )}
            </div>
        </div>
    );
}
