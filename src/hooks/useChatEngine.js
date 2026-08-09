import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { markdownToSpeech } from '../utils/markdownToSpeech';
import { stripAttachmentData, formatAttachmentSize } from '../utils/attachment';
import { buildApiUrl } from '../utils/url';
import { makeSessionStore } from '../lib/sessionStore';
import { executeToolCall, getToolDefinitions } from '../lib/chatTools';
import { buildChatHistory, buildPinPreamble } from './chatHistory';
import { addPin as addPinReducer, removePin as removePinReducer, MAX_PINS } from './pins';
import { buildRequestFields, INFERENCE_DEFAULTS, truncationMessage } from './inference';

const SENTENCE_TERMINATOR = /(?<=[.!?])\s+/;
const MIN_TTS_LENGTH = 5;
const MAX_TITLE_LENGTH = 60;

const newSessionId = () => `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const titleFromPrompt = (text) => {
    const oneLine = (text || '').replace(/\s+/g, ' ').trim();
    return oneLine.length > MAX_TITLE_LENGTH
        ? oneLine.slice(0, MAX_TITLE_LENGTH - 1) + '…'
        : (oneLine || 'New chat');
};

/**
 * Drives a streaming chat against a local Ollama server.
 *
 * Streaming mode reads NDJSON chunks from /api/chat, appends tokens to the
 * latest assistant message, and flushes complete sentences as they form.
 * After-complete mode skips per-token TTS and segments the full reply once
 * streaming finishes.
 *
 * TTS queue design (mirrors useTtsEngine's reader pattern):
 *   - Each enqueued sentence kicks off Kokoro synthesis IMMEDIATELY (parallel),
 *     storing the in-flight promise on the queue item.
 *   - A single playback loop awaits items in order — by the time it reaches
 *     item N+1, its synthesis is (likely) already complete, eliminating the
 *     audible gap that the old FIFO `then`-chain produced.
 *   - Single chat audio element is reused; volume/speed read at play time.
 */
export function useChatEngine({
    ollamaHost,
    ollamaPort,
    selectedModel,
    chatTtsMode,        // 'streaming' | 'after-complete'
    chatAutoTts,        // bool — disables TTS entirely
    inference = INFERENCE_DEFAULTS,  // per-model settings → think / keep_alive / options
    isLocalhost,        // bool — true means use Kokoro, false means Web Speech fallback
    selectedVoice,
    playbackSpeed,
    requestTimeout,
    apiHost,            // FastAPI host — used for chat-session persistence (same server as Kokoro)
    apiPort,
    currentDocId,             // sha256 of the open doc (null if none) — gates autonomous tools
    currentDocIndexState,     // 'indexed' | 'chunks_uploaded' | ... | null — gates autonomous tools
    synthesizeText,     // from useTtsEngine — returns Promise<blobUrl|null>
    playChatUrl,        // from useTtsEngine — plays a pre-fetched blob URL
    playChatSpeech,     // from useTtsEngine — Web Speech API fallback
    stopChatPlayback,   // from useTtsEngine — silences chat audio + speech synthesis
    showToast,
}) {
    // Toasted once per tool/thinking fallback so retries don't spam the user.
    const toolFallbackToastedRef = useRef(false);
    // Toasted once per session so a model that rejects thinking levels doesn't spam.
    const thinkLevelFallbackToastedRef = useRef(false);
    // Toast at most once per offline streak — avoid spamming on every save.
    const backendOfflineToastedRef = useRef(false);
    const sessionStore = useMemo(() => makeSessionStore({
        apiHost,
        apiPort,
        onBackendOffline: () => {
            if (backendOfflineToastedRef.current) return;
            backendOfflineToastedRef.current = true;
            showToast?.('Backend offline — chat sessions won’t be saved.', 4000);
        },
    }), [apiHost, apiPort, showToast]);
    const [messages, setMessages] = useState([]);
    const [pins, setPins] = useState([]);
    const pinsRef = useRef([]);
    pinsRef.current = pins;
    const [isStreaming, setIsStreaming] = useState(false);
    const [availableModels, setAvailableModels] = useState([]);
    const [reachable, setReachable] = useState(null); // null=unknown, true/false
    const [speakingMessageId, setSpeakingMessageId] = useState(null); // which assistant msg is being read aloud

    const abortRef = useRef(null);
    const sentenceBufferRef = useRef('');
    const messagesRef = useRef([]);
    messagesRef.current = messages;
    // Mirror of speakingMessageId for use inside async/then callbacks (avoids stale closures).
    const speakingMessageIdRef = useRef(null);
    const setSpeaking = (id) => { speakingMessageIdRef.current = id; setSpeakingMessageId(id); };

    // Prefetched playback queue. Each item:
    //   { kind: 'kokoro', urlPromise: Promise<blobUrl|null> }
    //   { kind: 'fallback', text: string }
    const chatQueueRef = useRef([]);
    const chatPlayingRef = useRef(false);
    const chatPlaybackPromiseRef = useRef(Promise.resolve());

    // Session state — list metadata stays in React state for the sidebar; the
    // currently-loaded session's full record (including events log) sits in a ref
    // because we mutate it on every event without re-rendering.
    const [sessions, setSessions] = useState([]); // metadata only (no messages/events)
    const [activeSessionId, setActiveSessionId] = useState(null);
    const activeSessionIdRef = useRef(null);
    const [events, setEvents] = useState([]); // events for the active session
    const eventsRef = useRef([]);              // mirror used inside async callbacks
    const createdAtRef = useRef(null);         // start timestamp of the active session

    // Empty `ollamaHost` returns a relative path — useful when the app sits
    // behind a reverse proxy that routes /api/* to the local Ollama daemon.
    const ollamaUrl = (path) => buildApiUrl(ollamaHost, ollamaPort, path);

    // Read latest values via refs so the playback loop and queued items pick up
    // voice/speed/volume changes for not-yet-fetched items without re-creating callbacks.
    const ttsParamsRef = useRef({});
    ttsParamsRef.current = { isLocalhost, selectedVoice, playbackSpeed, requestTimeout };

    // Build a queue item with synthesis DEFERRED. Synthesis is triggered lazily
    // by the playback loop's prefetch (only N..N+2 in flight at once), mirroring
    // the reader's prefetchBuffer pattern. This avoids piling up parallel
    // requests against Kokoro's serializing asyncio lock — which previously
    // caused cascading per-sentence timeouts on long replies.
    const makeQueueItem = useCallback((spoken) => {
        const { isLocalhost } = ttsParamsRef.current;
        if (!isLocalhost) return { kind: 'fallback', text: spoken, urlPromise: null };
        return { kind: 'kokoro', text: spoken, urlPromise: null };
    }, []);

    // Kick off synthesis for a queue item if it hasn't started yet. Idempotent.
    const kickoffSynthesis = useCallback((item) => {
        if (!item || item.kind !== 'kokoro' || item.urlPromise) return;
        const { selectedVoice, playbackSpeed, requestTimeout } = ttsParamsRef.current;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);
        item.urlPromise = synthesizeText(item.text, {
            voice: selectedVoice,
            speed: playbackSpeed,
            signal: controller.signal,
        }).finally(() => clearTimeout(timeoutId));
    }, [synthesizeText]);

    // Drain the queue serially. Each iteration:
    //   1. shifts the current item
    //   2. kicks off synthesis for current + next two (bounded prefetch)
    //   3. awaits the current item's URL and plays it
    // New items pushed mid-playback (streaming auto-TTS) are picked up automatically.
    const runChatPlayback = useCallback(async () => {
        while (chatQueueRef.current.length > 0) {
            if (!chatPlayingRef.current) return;
            const item = chatQueueRef.current.shift();
            try {
                if (item.kind === 'kokoro') {
                    kickoffSynthesis(item);
                    // Prefetch the next two so they're ready by the time the loop reaches them.
                    if (chatQueueRef.current[0]) kickoffSynthesis(chatQueueRef.current[0]);
                    if (chatQueueRef.current[1]) kickoffSynthesis(chatQueueRef.current[1]);
                    const url = await item.urlPromise;
                    if (!chatPlayingRef.current) {
                        if (url) URL.revokeObjectURL(url);
                        return;
                    }
                    if (!url) continue; // synthesis failed silently — skip and keep going
                    await playChatUrl(url);
                    URL.revokeObjectURL(url);
                } else {
                    if (!chatPlayingRef.current) return;
                    await playChatSpeech(item.text);
                }
            } catch (e) {
                console.error('Chat TTS playback error:', e);
            }
        }
        chatPlayingRef.current = false;
    }, [playChatUrl, playChatSpeech, kickoffSynthesis]);

    const ensurePlaybackRunning = useCallback(() => {
        if (chatPlayingRef.current) return;
        chatPlayingRef.current = true;
        chatPlaybackPromiseRef.current = runChatPlayback();
    }, [runChatPlayback]);

    const enqueueTts = useCallback((text) => {
        if (!chatAutoTts) return;
        // Strip markdown markup so the TTS doesn't vocalize "star star bold".
        const spoken = markdownToSpeech(text).trim();
        if (spoken.length < MIN_TTS_LENGTH) return;
        chatQueueRef.current.push(makeQueueItem(spoken));
        ensurePlaybackRunning();
    }, [chatAutoTts, makeQueueItem, ensurePlaybackRunning]);

    // Drop any pending Kokoro URLs in the queue when we cancel — they'd leak otherwise.
    const drainQueueAndRevoke = () => {
        const items = chatQueueRef.current;
        chatQueueRef.current = [];
        for (const item of items) {
            if (item.kind === 'kokoro' && item.urlPromise) {
                item.urlPromise.then(url => { if (url) URL.revokeObjectURL(url); }).catch(() => {});
            }
        }
    };

    // ----- SESSION MANAGEMENT -----
    const setActive = (id) => { activeSessionIdRef.current = id; setActiveSessionId(id); };

    // Persist pins immediately when a session already exists. Before the first
    // message there is no session yet; pins ride along in the first full save.
    const persistPins = useCallback((next) => {
        const id = activeSessionIdRef.current;
        if (id) sessionStore.updateSessionPins(id, next);
    }, [sessionStore]);

    const addPin = useCallback((pin) => {
        const res = addPinReducer(pinsRef.current, pin);
        if (!res.added) {
            const msg = res.reason === 'duplicate' ? 'That passage is already pinned.'
                : res.reason === 'max-pins' ? `You can pin up to ${MAX_PINS} passages.`
                : 'Pinned context is full — remove a pin first.';
            showToast?.(msg, 3500);
            return false;
        }
        setPins(res.pins);
        persistPins(res.pins);
        return true;
    }, [showToast, persistPins]);

    const removePin = useCallback((id) => {
        const next = removePinReducer(pinsRef.current, id);
        setPins(next);
        persistPins(next);
    }, [persistPins]);

    const clearPins = useCallback(() => setPins([]), []);

    const refreshSessions = useCallback(async () => {
        const list = await sessionStore.getRecentSessions();
        setSessions(list);
    }, [sessionStore]);

    // Append an event to the in-memory log for the active session.
    // Persisted alongside messages on next saveActiveSession() call.
    const logEvent = useCallback((kind, message) => {
        const entry = { ts: Date.now(), kind, message: message || '' };
        eventsRef.current = [...eventsRef.current, entry];
        setEvents(eventsRef.current);
    }, []);

    // Persist the active session — must be called with the latest messages array.
    const saveActiveSession = useCallback(async (overrides = {}) => {
        const id = activeSessionIdRef.current;
        if (!id) return;
        // Strip attachment binary payloads (dataUrl + base64) before persisting —
        // sessions stay cheap; reopening shows placeholder chips with metadata only.
        const persistableMessages = messagesRef.current.map(m => {
            if (!Array.isArray(m.attachments) || m.attachments.length === 0) return m;
            return { ...m, attachments: m.attachments.map(stripAttachmentData) };
        });
        const record = {
            id,
            title: overrides.title ?? sessions.find(s => s.id === id)?.title ?? 'New chat',
            model: overrides.model ?? selectedModel ?? '',
            createdAt: createdAtRef.current || Date.now(),
            messages: persistableMessages,
            events: eventsRef.current,
            pins: pinsRef.current,
        };
        const result = await sessionStore.saveSession(record);
        // If the save forked an old IDB session, switch the active id to the new
        // pg row so subsequent edits flow into the same record.
        if (result?.forkedFrom && result.id !== id) {
            setActive(result.id);
        }
        if (result) backendOfflineToastedRef.current = false;
        await refreshSessions();
    }, [sessions, selectedModel, refreshSessions, sessionStore]);

    // Load an existing session into the active view. Stops anything currently playing.
    const switchToSession = useCallback(async (id) => {
        if (!id) return;
        // Cancel anything in flight before swapping content.
        if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
        chatPlayingRef.current = false;
        drainQueueAndRevoke();
        stopChatPlayback();
        setSpeaking(null);
        sentenceBufferRef.current = '';

        const record = await sessionStore.getSession(id);
        if (!record) {
            // Stale id — drop it.
            setActive(null);
            eventsRef.current = [];
            setEvents([]);
            return;
        }
        setMessages(record.messages || []);
        setPins(record.pins || []);
        eventsRef.current = record.events || [];
        setEvents(eventsRef.current);
        createdAtRef.current = record.createdAt || Date.now();
        setActive(id);
        setIsStreaming(false);
    }, [stopChatPlayback, sessionStore]);

    // Reset the chat view without deleting any saved sessions.
    const newSession = useCallback(() => {
        if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
        chatPlayingRef.current = false;
        drainQueueAndRevoke();
        stopChatPlayback();
        setSpeaking(null);
        sentenceBufferRef.current = '';

        setMessages([]);
        setPins([]);
        eventsRef.current = [];
        setEvents([]);
        createdAtRef.current = null;
        setActive(null);
        setIsStreaming(false);
    }, [stopChatPlayback]);

    const deleteSession = useCallback(async (id) => {
        await sessionStore.deleteSession(id);
        await refreshSessions();
        if (activeSessionIdRef.current === id) {
            // The active session was deleted — start fresh.
            newSession();
        }
    }, [refreshSessions, newSession, sessionStore]);

    const renameSession = useCallback(async (id, newTitle) => {
        const ok = await sessionStore.renameSession(id, newTitle);
        if (ok) await refreshSessions();
    }, [refreshSessions, sessionStore]);

    // On mount, load the sessions list. Don't auto-load any session — start blank
    // (matches the "auto-create on first message" UX choice).
    useEffect(() => {
        refreshSessions();
    }, [refreshSessions]);

    // Flush any complete sentences from the rolling buffer; keep the trailing
    // partial fragment for the next chunk.
    const flushBufferedSentences = useCallback(() => {
        const buf = sentenceBufferRef.current;
        const parts = buf.split(SENTENCE_TERMINATOR);
        if (parts.length <= 1) return;
        // Last element is the (possibly incomplete) trailing fragment — keep it.
        sentenceBufferRef.current = parts[parts.length - 1];
        for (let i = 0; i < parts.length - 1; i++) {
            enqueueTts(parts[i]);
        }
    }, [enqueueTts]);

    // GET /api/tags — populate the model dropdown. Debounced when host/port changes.
    // Both being empty is valid: it means "use same origin" (reverse-proxied deploy).
    const refreshModels = useCallback(async () => {
        try {
            const controller = new AbortController();
            const t = setTimeout(() => controller.abort(), 5000);
            const res = await fetch(ollamaUrl('/api/tags'), { signal: controller.signal });
            clearTimeout(t);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const names = Array.isArray(data?.models) ? data.models.map(m => m.name) : [];
            setAvailableModels(names);
            setReachable(true);
        } catch (e) {
            console.warn('Ollama unreachable:', e.message);
            setAvailableModels([]);
            setReachable(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [ollamaHost, ollamaPort]);

    // Auto-refresh on host/port change (debounced so typing isn't a request storm)
    useEffect(() => {
        const handle = setTimeout(refreshModels, 400);
        return () => clearTimeout(handle);
    }, [refreshModels]);

    // Stop only the read-aloud — does NOT abort an in-flight Ollama stream.
    // Used by the per-message Stop button on assistant bubbles.
    const stopSpeaking = useCallback(() => {
        chatPlayingRef.current = false;     // signals runChatPlayback to exit on its next iteration
        drainQueueAndRevoke();
        stopChatPlayback();                 // pauses current audio + speech synthesis
        setSpeaking(null);
    }, [stopChatPlayback]);

    // Manually start (or restart) read-aloud for a specific assistant message.
    // Useful when chatAutoTts is off, or to re-read a finished message later.
    const speakMessage = useCallback((messageId) => {
        const msg = messagesRef.current.find(m => m.id === messageId);
        if (!msg || msg.role !== 'assistant' || !msg.content) return;

        // Cancel any current playback + queued sentences before starting fresh.
        stopSpeaking();

        const cleaned = markdownToSpeech(msg.content).trim();
        if (!cleaned) return;
        const sentences = cleaned
            .replace(/\s+/g, ' ')
            .split(SENTENCE_TERMINATOR)
            .filter(s => s.trim().length >= MIN_TTS_LENGTH);
        if (sentences.length === 0) return;

        setSpeaking(messageId);
        // Eagerly fire synthesis for ALL sentences in parallel — Kokoro serializes
        // server-side anyway, so the first finishes quickly and later ones overlap
        // with playback of earlier ones. No audible gaps.
        for (const s of sentences) {
            chatQueueRef.current.push(makeQueueItem(s));
        }
        ensurePlaybackRunning();
        // When the queue drains, clear the indicator — but only if we're still
        // speaking the same message (a later speakMessage / stopSpeaking may have replaced us).
        chatPlaybackPromiseRef.current.then(() => {
            if (speakingMessageIdRef.current === messageId) setSpeaking(null);
        });
    }, [stopSpeaking, makeQueueItem, ensurePlaybackRunning]);

    const stopStream = useCallback(() => {
        if (abortRef.current) {
            abortRef.current.abort();
            abortRef.current = null;
        }
        // Hard stop: silence current audio, drop any queued sentences, reset playback state.
        // The user wants silence, not a partial sentence finishing.
        stopSpeaking();
        sentenceBufferRef.current = '';
        setIsStreaming(false);
    }, [stopSpeaking]);

    // "Clear" in the sidebar starts a fresh blank session view. The active
    // session (if any) is preserved on disk and accessible via the sessions list.
    const clearHistory = useCallback(() => {
        newSession();
    }, [newSession]);

    const sendMessage = useCallback(async (userText, attachments = []) => {
        const trimmed = (userText || '').trim();
        const cleanAttachments = (attachments || []).filter(a => a && a.kind);
        const hasPins = pinsRef.current.length > 0;
        if (!trimmed && cleanAttachments.length === 0 && !hasPins) return;
        if (isStreaming) return;

        if (!selectedModel) {
            showToast?.('Pick a model first.', 4000);
            return;
        }

        // `Date.now()` only has ms resolution and these two messages are
        // created in the same tick — left to itself, both rows tie on
        // timestamp at the backend, then sort by id, which puts `a-<ts>`
        // before `u-<ts>` lexicographically and the pair renders flipped on
        // reload. Bumping the assistant ts (and id suffix) by 1 ms keeps the
        // ordering monotonic without a sequence counter.
        const userTs = Date.now();
        const assistantTs = userTs + 1;
        const userMsg = {
            role: 'user',
            content: trimmed,
            attachments: cleanAttachments,
            id: `u-${userTs}`,
            timestamp: userTs,
        };
        const assistantId = `a-${assistantTs}`;
        const assistantMsg = { role: 'assistant', content: '', thinking: '', id: assistantId, timestamp: assistantTs };

        // Lock TTS mode + inference settings for THIS message — changing them mid-stream applies to the next message.
        const modeForThisMsg = chatTtsMode;
        const inferenceForThisMsg = inference;

        // Auto-create a session if this is the first message.
        let sessionTitleSet = false;
        if (!activeSessionIdRef.current) {
            const id = newSessionId();
            createdAtRef.current = Date.now();
            eventsRef.current = [];
            setEvents([]);
            setActive(id);
            sessionTitleSet = true; // we'll set the title from this prompt below
        }

        setMessages(prev => [...prev, userMsg, assistantMsg]);
        sentenceBufferRef.current = '';
        setIsStreaming(true);
        logEvent('sent', `prompt: ${titleFromPrompt(trimmed || '(attachment only)')}`);
        if (cleanAttachments.length > 0) {
            const imgCount = cleanAttachments.filter(a => a.kind === 'image').length;
            const audCount = cleanAttachments.filter(a => a.kind === 'audio').length;
            const totalBytes = cleanAttachments.reduce((sum, a) => sum + (a.size || 0), 0);
            const parts = [];
            if (imgCount) parts.push(`${imgCount} image${imgCount > 1 ? 's' : ''}`);
            if (audCount) parts.push(`${audCount} audio`);
            logEvent('attached', `${parts.join(', ')} (${formatAttachmentSize(totalBytes)} total)`);
        }
        // Track this as the message currently being read aloud so the per-message
        // Stop button can target it. If auto-TTS is off, leave it null until the
        // user manually triggers speakMessage().
        if (chatAutoTts) setSpeaking(assistantId);

        const controller = new AbortController();
        abortRef.current = controller;

        try {
            // Build the messages history for the Ollama POST.
            //
            // Ollama's native /api/chat requires `content` to be a plain string
            // (structured content blocks return HTTP 400). The `Message` struct
            // only has ONE binary-input field: `images: [base64]`. There is no
            // `audios` field — earlier attempts using `audio` / `audios` were
            // silently dropped by the JSON parser.
            //
            // For audio-capable models (qwen2-audio, gemma4-with-audio, ...),
            // the convention is to put the audio bytes into the same `images`
            // array — the runtime treats those bytes as audio when the model
            // is audio-capable. So image AND audio attachments both feed
            // `images`. (If a single message mixes both kinds, the model will
            // see both byte blobs and decide based on its own modality. We
            // accept that edge case rather than introducing a non-existent
            // field.)
            //
            // Attachments stripped on session save (no base64) are skipped — the
            // historical message just goes back as text, which is fine.
            // (Message mapping + history assembly live in ./chatHistory so the
            // ordering is unit-tested — see buildChatHistory below.)
            //
            // Persistent pins → system preamble, placed right before the user
            // turn by buildChatHistory. Whole-document breadth comes from the
            // autonomous search_document tool, not from here.
            const contextPreamble = buildPinPreamble(pinsRef.current);
            const history = buildChatHistory({
                priorMessages: messagesRef.current,
                contextPreamble,
                userMsg,
            });

            // ---------- Autonomous tool calling ----------
            // Build the tool context once. The registry filters by `when(ctx)`,
            // so when there's no indexed doc loaded, `tools` is just [] and the
            // request body looks identical to the pre-tools era.
            const toolCtx = { currentDocId, currentDocIndexState, apiHost, apiPort };
            const tools = getToolDefinitions(toolCtx);

            // Consume a streaming /api/chat response. Token-by-token content
            // and thinking are flushed into the assistant message live; the
            // returned `captured` object reports the final stats + any
            // tool_calls the model decided to emit.
            const consumeStream = async (response) => {
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let lineBuf = '';
                const captured = { toolCalls: [], stats: null };

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    lineBuf += decoder.decode(value, { stream: true });
                    const lines = lineBuf.split('\n');
                    lineBuf = lines.pop() || '';

                    for (const line of lines) {
                        const trimmedLine = line.trim();
                        if (!trimmedLine) continue;
                        let payload;
                        try { payload = JSON.parse(trimmedLine); }
                        catch { continue; }

                        const token = payload?.message?.content || '';
                        const thinkingTok = payload?.message?.thinking || '';
                        const tcChunk = payload?.message?.tool_calls;

                        if (thinkingTok) {
                            // Reasoning trace — surfaces in the disclosure but never feeds TTS.
                            setMessages(prev => prev.map(m =>
                                m.id === assistantId ? { ...m, thinking: (m.thinking || '') + thinkingTok } : m
                            ));
                        }
                        if (token) {
                            setMessages(prev => prev.map(m =>
                                m.id === assistantId ? { ...m, content: m.content + token } : m
                            ));
                            if (modeForThisMsg === 'streaming') {
                                sentenceBufferRef.current += token;
                                flushBufferedSentences();
                            }
                        }
                        if (Array.isArray(tcChunk) && tcChunk.length > 0) {
                            captured.toolCalls.push(...tcChunk);
                        }
                        if (payload?.done) {
                            // Capture Ollama's per-request metrics from the final chunk.
                            // All durations are nanoseconds; the UI converts to s for display.
                            captured.stats = {
                                model: payload.model,
                                doneReason: payload.done_reason,
                                totalNs: payload.total_duration,
                                loadNs: payload.load_duration,
                                promptEvalCount: payload.prompt_eval_count,
                                promptEvalNs: payload.prompt_eval_duration,
                                evalCount: payload.eval_count,
                                evalNs: payload.eval_duration,
                            };
                            break;
                        }
                    }
                }
                return captured;
            };

            // First /api/chat request. If tools is non-empty, include it. Some
            // models 400 when `think: true` and `tools: [...]` are sent
            // together — fall back to a tools-less retry in that case (we
            // keep thinking on; only tools get dropped).
            const buildBody = (extra = {}) => ({
                model: selectedModel,
                messages: history,
                stream: true,
                ...buildRequestFields(inferenceForThisMsg),
                ...extra,
            });
            const firstBody = tools.length > 0 ? buildBody({ tools }) : buildBody();
            let res = await fetch(ollamaUrl('/api/chat'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(firstBody),
                signal: controller.signal,
            });
            if (!res.ok && tools.length > 0 && res.status >= 400 && res.status < 500) {
                console.warn(`Ollama returned ${res.status} with tools+think — retrying without tools.`);
                if (!toolFallbackToastedRef.current) {
                    toolFallbackToastedRef.current = true;
                    showToast?.('This model rejected tools — proceeded without them.', 4000);
                }
                logEvent('tool-fallback', `model rejected tools (HTTP ${res.status}); retrying without`);
                res = await fetch(ollamaUrl('/api/chat'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(buildBody()),
                    signal: controller.signal,
                });
            }
            // Some thinking-capable models accept the boolean but reject the
            // graduated levels. Retry once with `think: true` rather than
            // dropping thinking entirely.
            const usedLevel = !['off', 'on'].includes(inferenceForThisMsg.think);
            if (!res.ok && usedLevel && res.status >= 400 && res.status < 500) {
                console.warn(`Ollama returned ${res.status} for think:"${inferenceForThisMsg.think}" — retrying with think:true.`);
                if (!thinkLevelFallbackToastedRef.current) {
                    thinkLevelFallbackToastedRef.current = true;
                    showToast?.('This model rejected the thinking level — used plain thinking instead.', 4000);
                }
                logEvent('think-fallback', `model rejected think level "${inferenceForThisMsg.think}" (HTTP ${res.status})`);
                res = await fetch(ollamaUrl('/api/chat'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        model: selectedModel,
                        messages: history,
                        stream: true,
                        ...buildRequestFields({ ...inferenceForThisMsg, think: 'on' }),
                    }),
                    signal: controller.signal,
                });
            }
            if (!res.ok || !res.body) throw new Error(`Ollama error: HTTP ${res.status}`);

            const first = await consumeStream(res);
            if (first.stats) {
                setMessages(prev => prev.map(m =>
                    m.id === assistantId ? { ...m, stats: first.stats } : m
                ));
                // done_reason "length" means the context window filled up, not
                // that the model finished. Without this the only evidence is a
                // line inside a collapsed stats disclosure.
                const truncated = truncationMessage(first.stats);
                if (truncated) {
                    showToast?.(truncated, 7000);
                    logEvent('truncated', truncated);
                }
            }

            // If the model called any tools, execute them and re-POST without
            // `tools` to force a text answer. Single round-trip cap — we don't
            // loop, so a confused model can't spin forever.
            if (first.toolCalls.length > 0) {
                setMessages(prev => prev.map(m =>
                    m.id === assistantId ? { ...m, toolStatus: 'executing tool…' } : m
                ));
                logEvent('tool-call', `${first.toolCalls.length} tool call${first.toolCalls.length > 1 ? 's' : ''}`);

                const toolResults = await Promise.all(first.toolCalls.map(async (tc) => {
                    const name = tc?.function?.name;
                    const rawArgs = tc?.function?.arguments;
                    // Ollama may send arguments as object OR JSON string —
                    // normalize so the tool always receives a plain object.
                    let args = {};
                    if (rawArgs && typeof rawArgs === 'object') args = rawArgs;
                    else if (typeof rawArgs === 'string') {
                        try { args = JSON.parse(rawArgs); } catch { args = {}; }
                    }
                    const result = await executeToolCall(name, args, toolCtx);
                    return { name, arguments: args, result };
                }));

                // Persist a compact summary on the assistant message — full
                // chunk text isn't kept here (it's already in the model's
                // final answer; the disclosure just shows what was searched).
                const toolCallSummaries = toolResults.map(tr => ({
                    name: tr.name,
                    arguments: tr.arguments,
                    result_summary: tr.result?.error
                        ? { error: tr.result.error }
                        : {
                            ok: true,
                            chunk_count: tr.result?.agent_response?.chunk_count ?? null,
                            query: tr.result?.agent_response?.query ?? null,
                            summary_text: tr.result?.summary_text ?? null,
                        },
                }));
                setMessages(prev => prev.map(m =>
                    m.id === assistantId
                        ? { ...m, toolCalls: toolCallSummaries, toolStatus: undefined }
                        : m
                ));

                // Build the follow-up history per Ollama's tool-calling
                // convention: append the assistant turn (with its tool_calls
                // and any partial content it streamed) and one tool message
                // per result.
                const assistantContent = (messagesRef.current.find(m => m.id === assistantId)?.content) || '';
                const followupHistory = [
                    ...history,
                    { role: 'assistant', content: assistantContent, tool_calls: first.toolCalls },
                    ...toolResults.map(tr => ({
                        role: 'tool',
                        content: JSON.stringify(tr.result.agent_response || tr.result, null, 2),
                    })),
                ];

                const res2 = await fetch(ollamaUrl('/api/chat'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        model: selectedModel,
                        messages: followupHistory,
                        stream: true,
                        ...buildRequestFields(inferenceForThisMsg),
                    }),
                    signal: controller.signal,
                });
                if (!res2.ok || !res2.body) throw new Error(`Ollama follow-up error: HTTP ${res2.status}`);

                const second = await consumeStream(res2);
                if (second.stats) {
                    setMessages(prev => prev.map(m =>
                        m.id === assistantId ? { ...m, stats: second.stats } : m
                    ));
                    const truncated2 = truncationMessage(second.stats);
                    if (truncated2) {
                        showToast?.(truncated2, 7000);
                        logEvent('truncated', truncated2);
                    }
                }
            }

            // End-of-stream cleanup: flush any remaining buffered text.
            if (modeForThisMsg === 'streaming') {
                const tail = sentenceBufferRef.current.trim();
                if (tail) enqueueTts(tail);
                sentenceBufferRef.current = '';
            } else {
                // After-complete: segment the full reply now and queue it.
                const finalMsg = messagesRef.current.find(m => m.id === assistantId);
                if (finalMsg && chatAutoTts) {
                    const sentences = finalMsg.content
                        .replace(/\s+/g, ' ')
                        .split(SENTENCE_TERMINATOR)
                        .filter(s => s.trim().length >= MIN_TTS_LENGTH);
                    sentences.forEach(enqueueTts);
                }
            }
            // After all sentences are queued, fire-and-forget a tail handler that
            // clears the speaking indicator once the queue drains — but only if
            // we're still speaking this message (a manual override may have replaced us).
            if (chatAutoTts) {
                chatPlaybackPromiseRef.current.then(() => {
                    if (speakingMessageIdRef.current === assistantId) setSpeaking(null);
                });
            }
            setReachable(true);
            logEvent('received', `assistant reply (${(messagesRef.current.find(m => m.id === assistantId)?.content || '').length} chars)`);
        } catch (e) {
            if (e.name === 'AbortError') {
                logEvent('aborted', 'user stopped the stream');
            } else {
                console.error('Chat error:', e);
                setReachable(false);
                showToast?.(`Chat failed: ${e.message}`, 5000);
                logEvent('error', e.message);
            }
        } finally {
            abortRef.current = null;
            setIsStreaming(false);
            // Persist after each turn so the session list/log stay current.
            saveActiveSession({
                title: sessionTitleSet ? titleFromPrompt(trimmed) : undefined,
                model: selectedModel,
            }).catch(err => console.error('Session save failed:', err));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isStreaming, selectedModel, chatTtsMode, chatAutoTts, inference, ollamaHost, ollamaPort, apiHost, apiPort, currentDocId, currentDocIndexState, flushBufferedSentences, enqueueTts, logEvent, saveActiveSession]);

    const activeSession = sessions.find(s => s.id === activeSessionId) || null;

    return {
        messages,
        isStreaming,
        availableModels,
        reachable,
        sendMessage,
        stopStream,
        clearHistory,
        refreshModels,
        // Per-message read-aloud controls
        speakingMessageId,
        speakMessage,
        stopSpeaking,
        // Sessions + per-session event log
        sessions,
        activeSessionId,
        activeSession,
        events,
        newSession,
        switchToSession,
        deleteSession,
        renameSession,
        // Persistent pinned-context
        pins,
        addPin,
        removePin,
        clearPins,
    };
}
