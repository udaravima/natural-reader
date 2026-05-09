import { useCallback, useEffect, useRef, useState } from 'react';
import { markdownToSpeech } from '../utils/markdownToSpeech';

const SENTENCE_TERMINATOR = /(?<=[.!?])\s+/;
const MIN_TTS_LENGTH = 5;

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
    enableThinking,     // bool — sends `think: true` to Ollama and surfaces message.thinking
    isLocalhost,        // bool — true means use Kokoro, false means Web Speech fallback
    selectedVoice,
    playbackSpeed,
    requestTimeout,
    synthesizeText,     // from useTtsEngine — returns Promise<blobUrl|null>
    playChatUrl,        // from useTtsEngine — plays a pre-fetched blob URL
    playChatSpeech,     // from useTtsEngine — Web Speech API fallback
    stopChatPlayback,   // from useTtsEngine — silences chat audio + speech synthesis
    showToast,
}) {
    const [messages, setMessages] = useState([]);
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

    const ollamaUrl = (path) => `http://${ollamaHost}:${ollamaPort}${path}`;

    // Read latest values via refs so the playback loop and queued items pick up
    // voice/speed/volume changes for not-yet-fetched items without re-creating callbacks.
    const ttsParamsRef = useRef({});
    ttsParamsRef.current = { isLocalhost, selectedVoice, playbackSpeed, requestTimeout };

    // Drain the queue serially. Each iteration awaits the next item's already-in-flight
    // synthesis, then plays it. New items pushed during playback are picked up.
    const runChatPlayback = useCallback(async () => {
        while (chatQueueRef.current.length > 0) {
            if (!chatPlayingRef.current) return; // cancelled (stopSpeaking / speakMessage)
            const item = chatQueueRef.current.shift();
            try {
                if (item.kind === 'kokoro') {
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
    }, [playChatUrl, playChatSpeech]);

    const ensurePlaybackRunning = useCallback(() => {
        if (chatPlayingRef.current) return;
        chatPlayingRef.current = true;
        chatPlaybackPromiseRef.current = runChatPlayback();
    }, [runChatPlayback]);

    // Build a queue item by kicking off synthesis (or wrapping fallback text).
    const makeQueueItem = useCallback((spoken) => {
        const { isLocalhost, selectedVoice, playbackSpeed, requestTimeout } = ttsParamsRef.current;
        if (!isLocalhost) {
            return { kind: 'fallback', text: spoken };
        }
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);
        const urlPromise = synthesizeText(spoken, {
            voice: selectedVoice,
            speed: playbackSpeed,
            signal: controller.signal,
        }).finally(() => clearTimeout(timeoutId));
        return { kind: 'kokoro', urlPromise };
    }, [synthesizeText]);

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
            if (item.kind === 'kokoro') {
                item.urlPromise.then(url => { if (url) URL.revokeObjectURL(url); }).catch(() => {});
            }
        }
    };

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
    const refreshModels = useCallback(async () => {
        if (!ollamaHost || !ollamaPort) return;
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

    const clearHistory = useCallback(() => {
        stopStream();
        sentenceBufferRef.current = '';
        setMessages([]);
    }, [stopStream]);

    const sendMessage = useCallback(async (userText) => {
        const trimmed = userText?.trim();
        if (!trimmed || isStreaming) return;

        if (!selectedModel) {
            showToast?.('Pick a model first.', 4000);
            return;
        }

        const userMsg = { role: 'user', content: trimmed, id: `u-${Date.now()}`, timestamp: Date.now() };
        const assistantId = `a-${Date.now()}`;
        const assistantMsg = { role: 'assistant', content: '', thinking: '', id: assistantId, timestamp: Date.now() };

        // Lock TTS mode + thinking flag for THIS message — toggling mid-stream applies to next message.
        const modeForThisMsg = chatTtsMode;
        const thinkForThisMsg = !!enableThinking;

        setMessages(prev => [...prev, userMsg, assistantMsg]);
        sentenceBufferRef.current = '';
        setIsStreaming(true);
        // Track this as the message currently being read aloud so the per-message
        // Stop button can target it. If auto-TTS is off, leave it null until the
        // user manually triggers speakMessage().
        if (chatAutoTts) setSpeaking(assistantId);

        const controller = new AbortController();
        abortRef.current = controller;

        try {
            const history = [...messagesRef.current, userMsg].map(m => ({
                role: m.role, content: m.content,
            }));
            const res = await fetch(ollamaUrl('/api/chat'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model: selectedModel,
                    messages: history,
                    stream: true,
                    think: thinkForThisMsg,
                }),
                signal: controller.signal,
            });
            if (!res.ok || !res.body) throw new Error(`Ollama error: HTTP ${res.status}`);

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let lineBuf = '';

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
                    try {
                        payload = JSON.parse(trimmedLine);
                    } catch {
                        continue;
                    }
                    const token = payload?.message?.content || '';
                    const thinkingTok = payload?.message?.thinking || '';
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
                    if (payload?.done) break;
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
        } catch (e) {
            if (e.name === 'AbortError') {
                // Stop was called — assistant message keeps whatever streamed.
            } else {
                console.error('Chat error:', e);
                setReachable(false);
                showToast?.(`Chat failed: ${e.message}`, 5000);
            }
        } finally {
            abortRef.current = null;
            setIsStreaming(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isStreaming, selectedModel, chatTtsMode, chatAutoTts, enableThinking, ollamaHost, ollamaPort, flushBufferedSentences, enqueueTts]);

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
    };
}
