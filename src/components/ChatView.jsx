import { useEffect, useMemo, useRef, useState } from 'react';
import {
    Send, Square, Bot, User, Loader2, MessageSquare, Brain, ChevronDown, ChevronRight,
    Volume2, StopCircle, Copy, Paperclip, ImagePlus, Activity, FileText, X as XIcon,
    Wrench, Search, Download,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import AttachmentPreview from './AttachmentPreview';
// VoiceRecorder is intentionally not imported — audio attachments are paused
// until a Whisper-style transcription step is added (see plan file). The file
// itself is kept on disk so re-enabling is a single import + a JSX block.
import { fileToAttachment, IMAGE_ACCEPT, kindFromMime } from '../utils/attachment';

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
    downloadingMessageId,
    downloadMessageAudio,
    showToast,
    pins,
    onRemovePin,
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
    const [pendingAttachments, setPendingAttachments] = useState([]);
    const [isDragging, setIsDragging] = useState(false);
    const dragCounterRef = useRef(0); // robust drag-leave detection across child elements
    const listRef = useRef(null);
    const textareaRef = useRef(null);
    const fileInputRef = useRef(null);
    // Stick-to-bottom flag: when true, new messages/tokens auto-scroll the list to
    // the bottom (live "tailing"). When the user scrolls up, this flips to false
    // so they can read history without each token snapping them back down.
    const stickToBottomRef = useRef(true);
    const STICK_THRESHOLD = 80; // px from bottom to count as "at bottom"

    const handleListScroll = () => {
        const el = listRef.current;
        if (!el) return;
        const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
        stickToBottomRef.current = dist < STICK_THRESHOLD;
    };

    // Follow-along auto-scroll: only fires when the user is already near the bottom.
    useEffect(() => {
        if (!stickToBottomRef.current) return;
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

    // ------ Attachment helpers ------
    // Audio attachments are temporarily disabled — the Ollama image projector
    // can't decode audio bytes, and we haven't added a Whisper transcription
    // step yet. Audio files are filtered out here with a friendly toast.
    const ingestFiles = async (fileList) => {
        const all = Array.from(fileList || []);
        const dropped = all.filter(f => f && kindFromMime(f.type) === 'audio');
        if (dropped.length > 0) {
            showToast?.('Audio attachments are not supported yet — coming soon.', 4000);
        }
        const files = all.filter(f => f && kindFromMime(f.type) === 'image');
        if (files.length === 0) return;
        const results = await Promise.all(files.map(f => fileToAttachment(f)));
        const accepted = [];
        for (const r of results) {
            if (r.ok) accepted.push(r.attachment);
            else showToast?.(r.error, 4000);
        }
        if (accepted.length) {
            setPendingAttachments(prev => [...prev, ...accepted]);
        }
    };

    const removePending = (id) => {
        setPendingAttachments(prev => prev.filter(a => a.id !== id));
    };

    const handleFileChange = (e) => {
        ingestFiles(e.target.files);
        // Reset so picking the same file twice still triggers onChange
        e.target.value = '';
    };

    const handlePaste = (e) => {
        const items = e.clipboardData?.items;
        if (!items || items.length === 0) return;
        const filesFromClipboard = [];
        for (const it of items) {
            if (it.kind === 'file' && it.type.startsWith('image/')) {
                const f = it.getAsFile();
                if (f) filesFromClipboard.push(f);
            }
        }
        if (filesFromClipboard.length > 0) {
            e.preventDefault(); // we handled it; don't also paste a binary blob into the textarea
            ingestFiles(filesFromClipboard);
        }
    };

    // ------ Scoped drag & drop ------
    const handleDragEnter = (e) => {
        e.preventDefault(); e.stopPropagation();
        if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return;
        dragCounterRef.current += 1;
        setIsDragging(true);
    };
    const handleDragOver = (e) => {
        e.preventDefault(); e.stopPropagation();
    };
    const handleDragLeave = (e) => {
        e.preventDefault(); e.stopPropagation();
        dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
        if (dragCounterRef.current === 0) setIsDragging(false);
    };
    const handleDrop = (e) => {
        e.preventDefault(); e.stopPropagation();
        dragCounterRef.current = 0;
        setIsDragging(false);
        if (e.dataTransfer?.files?.length) ingestFiles(e.dataTransfer.files);
    };

    // A pin counts as send-worthy on its own (e.g. user clicked "Ask page"
    // and just wants the model's take without typing anything).
    const canSend = (draft.trim().length > 0 || pendingAttachments.length > 0 || pins.length > 0)
        && !isStreaming && !!selectedModel && reachable !== false;

    const handleSend = () => {
        if (!canSend) return;
        sendMessage(draft, pendingAttachments);
        setDraft('');
        setPendingAttachments([]);
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    return (
        <section
            className={`flex-1 flex flex-col overflow-hidden ${theme.viewportBg} transition-colors duration-300 relative`}
            onDragEnter={handleDragEnter}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
        >
            {/* Drop overlay — shown while dragging files over the chat view */}
            {isDragging && (
                <div className={`absolute inset-0 z-20 pointer-events-none flex items-center justify-center backdrop-blur-sm ${darkMode ? 'bg-blue-900/30' : 'bg-blue-100/60'}`}>
                    <div className={`px-6 py-4 rounded-2xl border-2 border-dashed ${theme.border} ${theme.bgSecondary} flex items-center gap-3 shadow-xl`}>
                        <ImagePlus size={20} className="text-blue-500" />
                        <span className={`text-sm font-bold ${theme.text}`}>Drop an image to attach</span>
                    </div>
                </div>
            )}

            {/* MESSAGE LIST */}
            <div
                ref={listRef}
                onScroll={handleListScroll}
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
                                    isDownloading={downloadingMessageId === m.id}
                                    onDownloadAudio={downloadMessageAudio ? () => downloadMessageAudio(m.id) : null}
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
                <div className="max-w-3xl mx-auto flex flex-col gap-2">
                    {/* Pin row — docs/pages/selections pinned via "Ask page" or
                        "Ask AI" on a selection. Persist across sends until
                        explicitly removed. */}
                    {pins.length > 0 && (
                        <div className="flex flex-col gap-1.5">
                            {pins.map((p) => (
                                <DocContextChip
                                    key={p.id}
                                    ctx={p}
                                    theme={theme}
                                    darkMode={darkMode}
                                    onRemove={() => onRemovePin?.(p.id)}
                                />
                            ))}
                        </div>
                    )}
                    {/* Pending attachments preview row */}
                    {pendingAttachments.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                            {pendingAttachments.map(att => (
                                <AttachmentPreview
                                    key={att.id}
                                    attachment={att}
                                    theme={theme}
                                    darkMode={darkMode}
                                    onRemove={() => removePending(att.id)}
                                />
                            ))}
                        </div>
                    )}

                    <div className="flex items-end gap-2">
                        {/* Paperclip — opens image picker (audio attachments
                            paused; see useChatEngine.js comment) */}
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            disabled={isStreaming}
                            className={`p-3 rounded-xl border ${theme.border} ${theme.bgTertiary} ${theme.textSecondary} hover:text-blue-500 transition-colors disabled:opacity-50`}
                            title="Attach image"
                        >
                            <Paperclip size={18} />
                        </button>
                        <input
                            type="file"
                            ref={fileInputRef}
                            onChange={handleFileChange}
                            accept={IMAGE_ACCEPT}
                            multiple
                            className="hidden"
                        />

                        <textarea
                            ref={textareaRef}
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={handleKeyDown}
                            onPaste={handlePaste}
                            placeholder={
                                reachable === false
                                    ? 'Ollama unreachable — check host/port in sidebar.'
                                    : !selectedModel
                                        ? 'Pick a model in the sidebar to start.'
                                        : 'Ask the model, drop / paste / attach an image…  (Enter to send)'
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
            </div>
        </section>
    );
}

function MessageBubble({
    message, theme, darkMode, isStreamingNow, isSpeaking,
    onSpeak, onStopSpeak, onCopy, isDownloading, onDownloadAudio,
}) {
    const isUser = message.role === 'user';
    const Icon = isUser ? User : Bot;
    const hasContent = !!message.content;
    const hasThinking = !isUser && !!message.thinking;
    const attachments = Array.isArray(message.attachments) ? message.attachments : [];
    const hasAttachments = attachments.length > 0;
    const docContext = isUser ? message.docContext : null;
    const toolStatus = !isUser ? message.toolStatus : null;
    const toolCalls = !isUser && Array.isArray(message.toolCalls) ? message.toolCalls : null;
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
                {hasAttachments && (
                    <div className={`flex flex-wrap gap-2 ${isUser ? 'justify-end' : ''}`}>
                        {attachments.map(att => (
                            <AttachmentPreview
                                key={att.id}
                                attachment={att}
                                theme={theme}
                                darkMode={darkMode}
                            />
                        ))}
                    </div>
                )}
                {docContext && (
                    <DocContextChip ctx={docContext} theme={theme} darkMode={darkMode} compact />
                )}
                {toolStatus && (
                    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-[11px] font-bold ${darkMode ? 'bg-cyan-500/15 text-cyan-300' : 'bg-cyan-50 text-cyan-700'}`}>
                        <Loader2 size={11} className="animate-spin" />
                        {toolStatus}
                    </div>
                )}
                {(hasContent || (isUser && !hasAttachments)) && (
                    <div className={`px-4 py-3 rounded-2xl ${isUser
                        ? darkMode ? 'bg-blue-600/30 text-white rounded-tr-sm' : 'bg-blue-100 text-slate-900 rounded-tr-sm'
                        : `${theme.bgSecondary} border ${theme.border} ${theme.text} rounded-tl-sm`
                    }`}>
                        {isUser ? (
                            hasContent ? (
                                <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                                    {message.content}
                                </p>
                            ) : (
                                <p className={`text-sm ${theme.textMuted} italic`}>(attachment only)</p>
                            )
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
                        {!isUser && onDownloadAudio && (
                            <button
                                onClick={onDownloadAudio}
                                disabled={isDownloading}
                                className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-bold transition-colors ${
                                    isDownloading
                                        ? `${theme.textMuted} opacity-70 cursor-wait`
                                        : `${theme.textMuted} hover:text-blue-500`
                                }`}
                                title={isDownloading ? 'Generating audio…' : 'Download audio (.wav)'}
                            >
                                {isDownloading
                                    ? <Loader2 size={12} className="animate-spin" />
                                    : <Download size={12} />}
                                <span>{isDownloading ? 'Generating' : 'Audio'}</span>
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
                {toolCalls && toolCalls.length > 0 && (
                    <ToolCallsDisclosure toolCalls={toolCalls} theme={theme} darkMode={darkMode} />
                )}
                {!isUser && message.stats && (
                    <MessageStatsDisclosure stats={message.stats} theme={theme} darkMode={darkMode} />
                )}
            </div>
        </div>
    );
}

// Compact, collapsed-by-default model-stats footer.
// Closed: a single line with token count, total time, and tokens/sec.
// Open: full breakdown — model, load, prompt eval, generation, done reason.
function MessageStatsDisclosure({ stats, theme, darkMode }) {
    const [open, setOpen] = useState(false);
    if (!stats) return null;

    const fmtNs = (ns) => {
        if (!ns && ns !== 0) return '—';
        const ms = ns / 1e6;
        if (ms < 1000) return `${Math.round(ms)} ms`;
        return `${(ms / 1000).toFixed(2)} s`;
    };
    const evalSec = stats.evalNs ? stats.evalNs / 1e9 : 0;
    const tokPerSec = evalSec > 0 && stats.evalCount
        ? (stats.evalCount / evalSec).toFixed(1)
        : null;

    const summaryParts = [];
    if (stats.evalCount != null) summaryParts.push(`${stats.evalCount} tok`);
    if (stats.totalNs != null) summaryParts.push(fmtNs(stats.totalNs));
    if (tokPerSec) summaryParts.push(`${tokPerSec} tok/s`);

    return (
        // `w-fit` keeps this disclosure sized to its content rather than stretching
        // the parent flex column. Without it, the inner grid's `1fr` (or any
        // intrinsic-grow child) makes the whole bubble expand to its max width.
        <div className={`w-fit max-w-full rounded-md ${darkMode ? 'bg-slate-800/30' : 'bg-slate-100/60'}`}>
            <button
                onClick={() => setOpen(o => !o)}
                className={`flex items-center gap-1.5 px-2 py-1 text-[10px] font-bold ${theme.textMuted} hover:text-blue-500 transition-colors`}
                title={open ? 'Hide details' : 'Show details'}
            >
                {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                <Activity size={11} />
                <span>{summaryParts.join(' · ') || 'details'}</span>
            </button>
            {open && (
                <dl className={`grid grid-cols-[max-content_max-content] gap-x-3 gap-y-0.5 px-3 pb-2 text-[10px] ${theme.textSecondary}`}>
                    {stats.model && (<>
                        <dt className={theme.textMuted}>Model</dt>
                        <dd className="font-mono">{stats.model}</dd>
                    </>)}
                    <dt className={theme.textMuted}>Total</dt>
                    <dd>{fmtNs(stats.totalNs)}</dd>
                    {stats.loadNs != null && (<>
                        <dt className={theme.textMuted}>Load</dt>
                        <dd>{fmtNs(stats.loadNs)}</dd>
                    </>)}
                    {stats.promptEvalCount != null && (<>
                        <dt className={theme.textMuted}>Prompt</dt>
                        <dd>{stats.promptEvalCount} tok in {fmtNs(stats.promptEvalNs)}</dd>
                    </>)}
                    {stats.evalCount != null && (<>
                        <dt className={theme.textMuted}>Generation</dt>
                        <dd>
                            {stats.evalCount} tok in {fmtNs(stats.evalNs)}
                            {tokPerSec && ` (${tokPerSec} tok/s)`}
                        </dd>
                    </>)}
                    {stats.doneReason && stats.doneReason !== 'stop' && (<>
                        <dt className={theme.textMuted}>Reason</dt>
                        <dd>{stats.doneReason}</dd>
                    </>)}
                </dl>
            )}
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

// Collapsed-by-default footer summarizing which tools the assistant called.
// Mirrors MessageStatsDisclosure's look so the bubble's footer-row treatments
// stay consistent. Click to expand and see per-tool args + a one-line result
// summary (e.g. "ok · 4 chunks" or "error: ...").
function ToolCallsDisclosure({ toolCalls, theme, darkMode }) {
    const [open, setOpen] = useState(false);
    if (!toolCalls?.length) return null;

    const summary = toolCalls.length === 1
        ? `🔎 ${toolCalls[0].name}`
        : `🔎 ${toolCalls.length} tool calls`;

    return (
        <div className={`w-fit max-w-full rounded-md ${darkMode ? 'bg-cyan-500/10' : 'bg-cyan-50'}`}>
            <button
                onClick={() => setOpen(o => !o)}
                className={`flex items-center gap-1.5 px-2 py-1 text-[10px] font-bold ${theme.textMuted} hover:text-cyan-600 transition-colors`}
                title={open ? 'Hide tool calls' : 'Show tool calls'}
            >
                {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                <Wrench size={11} />
                <span>{summary}</span>
            </button>
            {open && (
                <ul className={`px-3 pb-2 text-[10px] ${theme.textSecondary} space-y-1`}>
                    {toolCalls.map((tc, i) => {
                        const argStr = tc.arguments
                            ? Object.entries(tc.arguments)
                                .map(([k, v]) => `${k}=${typeof v === 'string' ? `"${v.length > 60 ? v.slice(0, 60) + '…' : v}"` : v}`)
                                .join(', ')
                            : '';
                        const result = tc.result_summary || {};
                        const resultLabel = result.error
                            ? `error: ${result.error}`
                            : result.chunk_count != null
                                ? `${result.chunk_count} chunk${result.chunk_count === 1 ? '' : 's'}`
                                : 'ok';
                        return (
                            <li key={i} className="flex items-start gap-1.5">
                                <Search size={10} className={`mt-0.5 shrink-0 ${theme.textMuted}`} />
                                <span className="font-mono break-all">
                                    <span className="font-bold">{tc.name}</span>({argStr}) <span className={theme.textMuted}>→ {resultLabel}</span>
                                </span>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
}

export function DocContextChip({ ctx, theme, darkMode, onRemove, compact = false }) {
    if (!ctx) return null;
    const label = ctx.kind === 'selection' ? 'Selection' : `Page ${ctx.page ?? '?'}`;
    const file = ctx.fileName || 'document';
    const preview = (ctx.text || '').replace(/\s+/g, ' ').slice(0, 120);
    return (
        <div
            className={`flex items-start gap-2 px-3 py-2 rounded-xl border text-xs ${theme.border} ${darkMode ? 'bg-purple-500/10' : 'bg-purple-50'} ${compact ? 'max-w-[85%]' : 'w-full'}`}
            title={ctx.text}
        >
            <FileText size={14} className={`shrink-0 mt-0.5 ${darkMode ? 'text-purple-300' : 'text-purple-600'}`} />
            <div className="flex-1 min-w-0">
                <p className={`font-bold truncate ${theme.text}`}>
                    {label} <span className={`font-normal ${theme.textMuted}`}>· {file}</span>
                </p>
                {preview && (
                    <p className={`text-[11px] mt-0.5 truncate ${theme.textSecondary}`}>{preview}{ctx.text.length > 120 ? '…' : ''}</p>
                )}
            </div>
            {onRemove && (
                <button
                    onClick={onRemove}
                    className={`shrink-0 p-1 rounded ${theme.hover} ${theme.textMuted} hover:text-red-500 transition-colors`}
                    title="Remove context"
                >
                    <XIcon size={12} />
                </button>
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
