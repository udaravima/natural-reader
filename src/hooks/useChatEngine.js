import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { markdownToSpeech } from '../utils/markdownToSpeech';
import { apiFetch } from '../utils/apiFetch';
import { makeSessionStore } from '../lib/sessionStore';
import { MODELS_PATH, budgetDetail, formatResetAt } from '../lib/chatTransport';
import { postTurn, readEvents } from '../lib/chatStream';
import { applyEvent } from '../lib/chatEvents';
import { describeRefusal } from '../lib/apiErrors';
import { addPin as addPinReducer, removePin as removePinReducer, MAX_PINS } from './pins';
import { INFERENCE_DEFAULTS, toWireSettings, truncationMessage } from './inference';
import { supportedKnobs } from '../lib/modelIds';

const SENTENCE_TERMINATOR = /(?<=[.!?])\s+/;
const MIN_TTS_LENGTH = 5;
const POLL_MS = 3000;   // how often a reloaded "still generating…" chat re-reads the server
const CHAT_NOT_FOUND = "This chat doesn't exist or you don't have access.";

const newSessionId = () => `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const browserTimezone = () => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; }
};

/**
 * Chat state for the SPA. The SERVER runs each turn (C1 spec §5): this hook
 * posts the new message, reads the typed event stream (lib/chatStream) and
 * renders it through the pure reducer (lib/chatEvents). What stays here: the
 * session list and switching, pins, and the read-aloud queue.
 *
 * TTS queue design (mirrors useTtsEngine's reader pattern):
 *   - Each enqueued sentence's synthesis starts lazily, at most N..N+2 in flight.
 *   - A single playback loop awaits items in order, so there's no audible gap.
 *   - Single chat audio element is reused; volume/speed read at play time.
 */
export function useChatEngine({
    selectedModel,
    chatTtsMode,        // 'streaming' | 'after-complete'
    chatAutoTts,        // bool — disables TTS entirely
    inference = INFERENCE_DEFAULTS,  // this model's settings → the turn's `settings`
    isLocalhost,        // bool — true means use Kokoro, false means Web Speech fallback
    selectedVoice,
    playbackSpeed,
    requestTimeout,
    apiHost,            // FastAPI host — chat turns, sessions and models
    apiPort,
    currentDocId,       // sha256 of the open doc (null if none) — the server decides what to do with it
    synthesizeText,     // from useTtsEngine — returns Promise<blobUrl|null>
    playChatUrl,        // from useTtsEngine — plays a pre-fetched blob URL
    playChatSpeech,     // from useTtsEngine — Web Speech API fallback
    stopChatPlayback,   // from useTtsEngine — silences chat audio + speech synthesis
    showToast,
}) {
    // Toast at most once per offline streak; reset when the server answers again.
    const backendOfflineToastedRef = useRef(false);
    const sessionStore = useMemo(() => makeSessionStore({
        apiHost,
        apiPort,
        onBackendOffline: () => {
            if (backendOfflineToastedRef.current) return;
            backendOfflineToastedRef.current = true;
            showToast?.("Chat is offline — can't reach the server.", 4000);
        },
    }), [apiHost, apiPort, showToast]);
    const [messages, setMessages] = useState([]);
    const [pins, setPins] = useState([]);
    const pinsRef = useRef([]);
    pinsRef.current = pins;
    const [isStreaming, setIsStreaming] = useState(false);
    const [availableModels, setAvailableModels] = useState([]);
    const availableModelsRef = useRef([]);   // read inside sendMessage without re-creating it
    availableModelsRef.current = availableModels;
    // Daily inference budget from the gateway (server mode only): null =
    // unknown or unlimited; {remaining_tokens, reset_at} otherwise.
    const [inferenceBudget, setInferenceBudget] = useState(null);
    const [reachable, setReachable] = useState(null); // null=unknown, true/false
    const [speakingMessageId, setSpeakingMessageId] = useState(null); // which assistant msg is being read aloud

    const abortRef = useRef(null);
    // True from Send until the turn settles, including a legacy chat's import:
    // `isStreaming` is state, so a double Send in one render would miss it.
    const sendingRef = useRef(false);
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
    // message there is no server session yet; pins ride along on each turn's
    // `session.pins`, which the server uses when that turn creates the chat.
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

    // The server writes the chat's log (sent, tool calls, errors…). Re-read it
    // after a turn so the sidebar's Log is current.
    const refreshEvents = useCallback(async (id) => {
        const record = await sessionStore.getSession(id);
        if (record && activeSessionIdRef.current === id) {
            eventsRef.current = record.events || [];
            setEvents(eventsRef.current);
        }
    }, [sessionStore]);

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

    // Every configured provider's models: [{id, provider, kind, name, capabilities}].
    const refreshModels = useCallback(async () => {
        try {
            const controller = new AbortController();
            const t = setTimeout(() => controller.abort(), 8000);
            const res = await apiFetch(apiHost, apiPort, MODELS_PATH, { signal: controller.signal });
            clearTimeout(t);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            setAvailableModels(Array.isArray(data?.models) ? data.models.filter((m) => m && m.id) : []);
            setReachable(true);
            backendOfflineToastedRef.current = false;
            setInferenceBudget(data?.budget ?? null);
        } catch (e) {
            console.warn('Model list unavailable:', e.message);
            setAvailableModels([]);
            setReachable(false);
        }
    }, [apiHost, apiPort]);

    // Auto-refresh on host/port change (debounced so typing isn't a request storm)
    useEffect(() => {
        const handle = setTimeout(refreshModels, 400);
        return () => clearTimeout(handle);
    }, [refreshModels]);

    // Stop only the read-aloud — does NOT abort the reply the server is streaming.
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
        const cleanAttachments = (attachments || []).filter((a) => a && a.kind);
        if (!trimmed && cleanAttachments.length === 0 && pinsRef.current.length === 0) return { sent: false };
        if (isStreaming || sendingRef.current) return { sent: false };
        const giveBack = { sent: false, refused: true, text: userText, attachments };
        if (!selectedModel) {
            showToast?.('Pick a model first.', 4000);
            return giveBack;
        }

        // Lock TTS mode + inference settings for THIS message.
        const modeForThisMsg = chatTtsMode;
        const inferenceForThisMsg = inference;

        // Which server session gets this turn?
        let sessionId = activeSessionIdRef.current;
        let isNew = false;
        if (sessionId && sessionStore.isLocalId(sessionId)) {
            // A pre-Postgres chat kept only in this browser: copy it to the
            // server (each continue of the browser copy makes a new server
            // chat, like today's fork-on-edit), then continue it there.
            const legacyId = sessionId;
            sendingRef.current = true;
            setIsStreaming(true);
            let forkedId = null;
            try {
                forkedId = await sessionStore.importLegacy(legacyId);
            } finally {
                sendingRef.current = false;
                setIsStreaming(false);
            }
            if (forkedId) refreshSessions();   // the copy shows in the sidebar whatever happens next
            if (activeSessionIdRef.current !== legacyId) {
                // The user switched chats during the copy: don't hijack that view.
                return giveBack;
            }
            if (!forkedId) {
                showToast?.("Couldn't copy this older chat to the server.", 5000);
                return giveBack;
            }
            sessionId = forkedId;
            setActive(forkedId);
        }
        if (!sessionId) {
            sessionId = newSessionId();
            isNew = true;
            setActive(sessionId);
            eventsRef.current = [];
            setEvents([]);
        }

        const now = Date.now();
        let userId = `u-local-${now}`;
        let assistantId = `a-local-${now}`;
        setMessages((prev) => [...prev,
            { role: 'user', content: trimmed, attachments: cleanAttachments, id: userId, timestamp: now },
            { role: 'assistant', content: '', thinking: '', id: assistantId, timestamp: now + 1, status: 'streaming' }]);
        sentenceBufferRef.current = '';
        sendingRef.current = true;
        setIsStreaming(true);
        if (chatAutoTts) setSpeaking(assistantId);
        const controller = new AbortController();
        abortRef.current = controller;
        const updateAssistant = (fn) => setMessages((prev) => prev.map((m) => (m.id === assistantId ? fn(m) : m)));

        const body = {
            message: {
                content: trimmed,
                attachments: cleanAttachments.filter((a) => a.base64).map((a) => ({
                    id: a.id, kind: a.kind, mime: a.mimeType, name: a.name || '', size: a.size || 0, base64: a.base64,
                })),
            },
            model: selectedModel,
            settings: toWireSettings(inferenceForThisMsg,
                supportedKnobs(availableModelsRef.current.find((m) => m.id === selectedModel))),
            context: { doc_id: currentDocId || null, timezone: browserTimezone() },
            // Always sent: the server uses it only when this turn creates the chat
            // (a retry after a first send that failed on the network included).
            session: { pins: pinsRef.current },
        };

        let refused = false;
        let completed = false;
        let replyText = '';
        try {
            const res = await postTurn({ apiHost, apiPort, sessionId, body, signal: controller.signal });
            if (!res.ok) {
                refused = true;
                const budget = await budgetDetail(res.clone());
                if (budget) {
                    setInferenceBudget(budget);
                    showToast?.(`Daily inference budget exhausted — resets at ${formatResetAt(budget.reset_at)}`, 6000);
                } else {
                    showToast?.(await describeRefusal(res, { notFound: CHAT_NOT_FOUND, tooLarge: 'Attachments' }), 5000);
                }
            } else {
                for await (const ev of readEvents(res.body)) {
                    if (ev.type === 'start') {
                        // Adopt the server's ids so reload, read-aloud and pins line up.
                        const [oldUser, oldAssistant] = [userId, assistantId];
                        setMessages((prev) => prev.map((m) => (
                            m.id === oldUser ? { ...m, id: ev.userMessageId }
                                : m.id === oldAssistant ? { ...m, id: ev.messageId } : m)));
                        if (speakingMessageIdRef.current === oldAssistant) setSpeaking(ev.messageId);
                        userId = ev.userMessageId;
                        assistantId = ev.messageId;
                        continue;
                    }
                    updateAssistant((m) => applyEvent(m, ev));
                    if (ev.type === 'text-start' && replyText) {
                        // A later step's text is a new paragraph (as applyEvent
                        // does); read aloud, it also ends the sentence before it.
                        replyText += '\n\n';
                        if (modeForThisMsg === 'streaming') {
                            sentenceBufferRef.current += '\n\n';
                            flushBufferedSentences();
                        }
                    } else if (ev.type === 'text-delta') {
                        replyText += ev.delta || '';
                        if (modeForThisMsg === 'streaming') {
                            sentenceBufferRef.current += ev.delta || '';
                            flushBufferedSentences();
                        }
                    } else if (ev.type === 'data-notice') {
                        showToast?.(ev.message, 4000);
                    } else if (ev.type === 'finish') {
                        completed = true;
                        const truncated = truncationMessage(ev.stats, inferenceForThisMsg);
                        if (truncated) showToast?.(truncated, 7000);
                    } else if (ev.type === 'error') {
                        if (ev.code === 'budget_exhausted') {
                            showToast?.('Daily inference budget exhausted.', 6000);
                            refreshModels();   // re-reads the budget readout
                        } else {
                            showToast?.(`Chat failed: ${ev.message}`, 5000);
                        }
                    }
                }
                setReachable(true);
                backendOfflineToastedRef.current = false;
            }
            if (completed) {
                if (modeForThisMsg === 'streaming') {
                    const tail = sentenceBufferRef.current.trim();
                    if (tail) enqueueTts(tail);
                    sentenceBufferRef.current = '';
                } else if (chatAutoTts) {
                    replyText.replace(/\s+/g, ' ').split(SENTENCE_TERMINATOR)
                        .filter((s) => s.trim().length >= MIN_TTS_LENGTH)
                        .forEach(enqueueTts);
                }
            }
        } catch (e) {
            if (e.name === 'AbortError') {
                updateAssistant((m) => ({ ...m, status: 'aborted', finishReason: 'aborted', toolStatus: undefined }));
            } else {
                console.error('Chat error:', e);
                setReachable(false);
                showToast?.(`Chat failed: ${e.message}`, 5000);
                updateAssistant((m) => ({ ...m, status: 'error', toolStatus: undefined }));
            }
        } finally {
            abortRef.current = null;
            sendingRef.current = false;
            setIsStreaming(false);
            if (refused) {
                const localIds = new Set([userId, assistantId]);
                setMessages((prev) => prev.filter((m) => !localIds.has(m.id)));
                if (isNew) setActive(null);
                setSpeaking(null);
            } else {
                // However the reply ended (finished, error event, dropped stream),
                // clear the read-aloud indicator once anything queued has played.
                if (chatAutoTts) {
                    const spokenId = assistantId;
                    chatPlaybackPromiseRef.current.then(() => {
                        if (speakingMessageIdRef.current === spokenId) setSpeaking(null);
                    });
                }
                refreshSessions();
                refreshEvents(sessionId);
            }
        }
        return refused ? giveBack : { sent: true };
    }, [isStreaming, selectedModel, chatTtsMode, chatAutoTts, inference, apiHost, apiPort, currentDocId,
        sessionStore, showToast, flushBufferedSentences, enqueueTts, refreshSessions, refreshEvents, refreshModels]);

    // A reloaded chat whose reply the server is still writing: re-read it until
    // it settles. Server-side stale recovery (spec §5.5) guarantees it does,
    // even if the worker writing it died.
    const hasRemoteStreaming = !isStreaming && messages.some((m) => m.status === 'streaming');
    useEffect(() => {
        if (!hasRemoteStreaming || !activeSessionId) return undefined;
        const id = activeSessionId;
        const handle = setInterval(async () => {
            const record = await sessionStore.getSession(id);
            if (!record || activeSessionIdRef.current !== id) return;
            if (abortRef.current) return;   // a local send started meanwhile: its stream owns the view
            setMessages(record.messages || []);
            eventsRef.current = record.events || [];
            setEvents(eventsRef.current);
        }, POLL_MS);
        return () => clearInterval(handle);
    }, [hasRemoteStreaming, activeSessionId, sessionStore]);

    const activeSession = sessions.find(s => s.id === activeSessionId) || null;

    return {
        messages,
        isStreaming,
        availableModels,
        inferenceBudget,
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
