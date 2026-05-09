import { useEffect, useMemo, useRef, useState } from 'react';
import { Send, Square, Bot, User, Loader2, MessageSquare, Brain, ChevronDown, ChevronRight, Volume2, StopCircle, Copy } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

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
    speakingMessageId,
    speakMessage,
    stopSpeaking,
    showToast,
}) {
    const copyMessage = async (text) => {
        if (!text) return;
        try {
            await navigator.clipboard.writeText(text);
            showToast?.('Copied to clipboard', 1500);
        } catch (e) {
            console.error('Clipboard write failed:', e);
            showToast?.('Copy failed — clipboard blocked', 3000);
        }
    };
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
                        {messages.map((m, i) => {
                            const isLatestAssistant = m.role === 'assistant' && i === messages.length - 1;
                            return (
                                <MessageBubble
                                    key={m.id}
                                    message={m}
                                    theme={theme}
                                    darkMode={darkMode}
                                    isStreamingNow={isStreaming && isLatestAssistant}
                                    isSpeaking={speakingMessageId === m.id}
                                    onSpeak={() => speakMessage(m.id)}
                                    onStopSpeak={stopSpeaking}
                                    onCopy={() => copyMessage(m.content)}
                                />
                            );
                        })}
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

function MessageBubble({ message, theme, darkMode, isStreamingNow, isSpeaking, onSpeak, onStopSpeak, onCopy }) {
    const isUser = message.role === 'user';
    const Icon = isUser ? User : Bot;
    const hasContent = !!message.content;
    const hasThinking = !isUser && !!message.thinking;
    return (
        <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white shrink-0 ${
                isUser
                    ? 'bg-gradient-to-br from-slate-600 to-slate-800'
                    : 'bg-gradient-to-br from-blue-500 to-cyan-600'
            }`}>
                <Icon size={16} />
            </div>
            <div className={`max-w-[85%] flex flex-col gap-2 ${isUser ? 'items-end' : 'items-start'}`}>
                {hasThinking && (
                    <ThinkingDisclosure
                        text={message.thinking}
                        theme={theme}
                        darkMode={darkMode}
                        isStreamingNow={isStreamingNow}
                    />
                )}
                {(hasContent || isUser) && (
                    <div className={`px-4 py-3 rounded-2xl ${isUser
                        ? darkMode ? 'bg-blue-600/30 text-white rounded-tr-sm' : 'bg-blue-100 text-slate-900 rounded-tr-sm'
                        : `${theme.bgSecondary} border ${theme.border} ${theme.text} rounded-tl-sm`
                    }`}>
                        {isUser ? (
                            <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                                {message.content}
                            </p>
                        ) : hasContent ? (
                            <AssistantMarkdown content={message.content} darkMode={darkMode} />
                        ) : (
                            <p className={`text-sm ${theme.textMuted} italic`}>…</p>
                        )}
                    </div>
                )}
                {hasContent && (
                    <div className="flex items-center gap-1">
                        {!isUser && (
                            <button
                                onClick={isSpeaking ? onStopSpeak : onSpeak}
                                className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-bold transition-colors ${isSpeaking
                                    ? 'text-red-500 hover:text-red-600'
                                    : `${theme.textMuted} hover:text-blue-500`}`}
                                title={isSpeaking ? 'Stop reading' : 'Read aloud'}
                            >
                                {isSpeaking ? <StopCircle size={12} /> : <Volume2 size={12} />}
                                <span>{isSpeaking ? 'Stop' : 'Read aloud'}</span>
                            </button>
                        )}
                        <button
                            onClick={onCopy}
                            className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-bold transition-colors ${theme.textMuted} hover:text-blue-500`}
                            title="Copy message"
                        >
                            <Copy size={12} />
                            <span>Copy</span>
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}

// Memoize the markdown component overrides — prevents react-markdown
// from rebuilding the renderer tree on every streaming token.
function AssistantMarkdown({ content, darkMode }) {
    const components = useMemo(() => ({
        p: ({ children }) => (
            <p className="my-1 text-sm leading-relaxed break-words">{children}</p>
        ),
        a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-500 underline break-all">
                {children}
            </a>
        ),
        code: ({ inline, children }) => inline
            ? <code className={`px-1 py-0.5 rounded text-xs font-mono ${darkMode ? 'bg-slate-700/60' : 'bg-slate-200'}`}>{children}</code>
            : <code className="font-mono text-xs">{children}</code>,
        pre: ({ children }) => (
            <pre className={`my-2 p-3 rounded-lg overflow-x-auto text-xs ${darkMode ? 'bg-slate-800/80 text-slate-100' : 'bg-slate-900/90 text-slate-100'}`}>
                {children}
            </pre>
        ),
        ul: ({ children }) => <ul className="list-disc pl-5 my-1 text-sm leading-relaxed">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal pl-5 my-1 text-sm leading-relaxed">{children}</ol>,
        li: ({ children }) => <li className="my-0.5">{children}</li>,
        h1: ({ children }) => <h1 className="text-base font-bold mt-2 mb-1">{children}</h1>,
        h2: ({ children }) => <h2 className="text-sm font-bold mt-2 mb-1">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-semibold mt-1.5 mb-1">{children}</h3>,
        blockquote: ({ children }) => (
            <blockquote className={`border-l-2 pl-3 my-1 italic ${darkMode ? 'border-slate-500 text-slate-300' : 'border-slate-400 text-slate-700'}`}>{children}</blockquote>
        ),
        table: ({ children }) => (
            <div className="overflow-x-auto my-2">
                <table className="text-xs border-collapse">{children}</table>
            </div>
        ),
        th: ({ children }) => <th className="border border-slate-500/40 px-2 py-1 font-semibold">{children}</th>,
        td: ({ children }) => <td className="border border-slate-500/40 px-2 py-1">{children}</td>,
    }), [darkMode]);

    return (
        <div className="text-sm leading-relaxed">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                {content}
            </ReactMarkdown>
        </div>
    );
}

function ThinkingDisclosure({ text, theme, darkMode, isStreamingNow }) {
    // Auto-expanded while this assistant message is still streaming, auto-collapsed
    // once streaming ends — unless the user has explicitly toggled, in which case we
    // honor their choice. Derived without an effect (see react-hooks/set-state-in-effect).
    const [userOverride, setUserOverride] = useState(null); // null | true | false
    const open = userOverride !== null ? userOverride : isStreamingNow;

    return (
        <div className={`max-w-full rounded-xl border ${theme.border} ${darkMode ? 'bg-slate-800/40' : 'bg-slate-100/70'} overflow-hidden`}>
            <button
                onClick={() => setUserOverride(!open)}
                className={`w-full flex items-center gap-2 px-3 py-2 text-[10px] font-bold uppercase tracking-wider ${theme.textMuted} hover:text-blue-500 transition-colors`}
            >
                {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <Brain size={12} />
                <span>{open ? 'Thinking' : 'Show thinking'}</span>
                {isStreamingNow && <Loader2 size={10} className="animate-spin ml-auto" />}
            </button>
            {open && (
                <div className={`px-3 pb-3 pt-0 text-[11px] italic leading-relaxed whitespace-pre-wrap break-words ${theme.textMuted}`}>
                    {text}
                </div>
            )}
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
