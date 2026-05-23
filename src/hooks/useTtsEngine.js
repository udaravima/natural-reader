import { useState, useEffect, useRef, useCallback } from 'react';
import { KOKORO_VOICES } from '../constants';
import { buildApiUrl } from '../utils/url';
import { markdownToSpeech } from '../utils/markdownToSpeech';
import { concatWavs } from '../utils/wavConcat';

/**
 * Manages TTS playback engine: audio caching, buffering, playback loop,
 * voice preview, selection reading, and audio download.
 */
export function useTtsEngine({
    textItems,
    currentSentenceIndex, setCurrentSentenceIndex,
    playbackIndexRef,
    currentPage, setCurrentPage,
    numPages,
    selectedVoice,
    playbackSpeed,
    isLocalhost,
    volume,
    apiHost,
    apiPort,
    requestTimeout,
    unlimitedBatchTimeout,
    backendAvailable,
    pdfFileName,
    setStatus, showToast,
    enabled = true,
}) {
    const [isPlaying, setIsPlaying] = useState(false);
    const [isDownloading, setIsDownloading] = useState(false);
    const [isReadingSelection, setIsReadingSelection] = useState(false);
    const [isPreviewingVoice, setIsPreviewingVoice] = useState(false);
    // ID of the chat message currently being synthesized for download.
    // Lets each message bubble show its own spinner without blocking the
    // (separate) page-audio download state above.
    const [downloadingMessageId, setDownloadingMessageId] = useState(null);
    // Whole-book audiobook export: long-running, per-page synthesis. Kept
    // separate from per-page/per-message download states so the UI can show
    // a dedicated progress + cancel surface.
    //
    //   { current, total, label } | null
    const [bookProgress, setBookProgress] = useState(null);
    const bookAbortRef = useRef(null);

    const audioCache = useRef(new Map());
    const pendingRequests = useRef(new Map()); // Track in-flight fetches to avoid duplicates
    const audioRef = useRef(new Audio());
    const voicePreviewRef = useRef(new Audio());
    const chatAudioRef = useRef(new Audio()); // Separate channel so chat TTS can't collide with reader TTS
    const retryCountRef = useRef(0);

    // Helper to build API URL — empty `apiHost` produces a relative URL so
    // reverse-proxied deployments (e.g. nginx routing /v1/* → Kokoro) work
    // without needing host/port in settings.
    const getApiUrl = (endpoint) => buildApiUrl(apiHost, apiPort, endpoint);

    // Pure synthesis: returns a blob URL (or null on error). Reused by reader playback,
    // selection read, voice preview, and chat sentence playback.
    const synthesizeText = useCallback(async (text, { voice, speed, signal } = {}) => {
        try {
            const url = buildApiUrl(apiHost, apiPort, '/v1/synthesize');
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text,
                    voice: voice || selectedVoice,
                    speed: speed || playbackSpeed,
                }),
                signal,
            });
            if (!response.ok) throw new Error('TTS Fail');
            const data = await response.json();
            const b64 = data.audio_base64 || data.audio;
            const blob = await (await fetch(`data:audio/wav;base64,${b64}`)).blob();
            return URL.createObjectURL(blob);
        } catch (err) {
            if (err.name !== 'AbortError') console.error('Synthesis error:', err);
            return null;
        }
    }, [apiHost, apiPort, selectedVoice, playbackSpeed]);

    // Play a single block of text on the chat audio channel, resolving when it ends.
    // Falls back to SpeechSynthesisUtterance when Kokoro isn't selected.
    const playSentence = useCallback((text) => {
        return new Promise((resolve) => {
            if (!text || !text.trim()) { resolve(); return; }

            if (!isLocalhost) {
                const ut = new SpeechSynthesisUtterance(text);
                ut.rate = playbackSpeed;
                ut.volume = volume;
                ut.onend = () => resolve();
                ut.onerror = () => resolve();
                window.speechSynthesis.speak(ut);
                return;
            }

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);
            synthesizeText(text, {
                voice: selectedVoice,
                speed: playbackSpeed,
                signal: controller.signal,
            }).then((url) => {
                clearTimeout(timeoutId);
                if (!url) { resolve(); return; }
                chatAudioRef.current.volume = volume;
                chatAudioRef.current.src = url;
                chatAudioRef.current.onended = () => {
                    URL.revokeObjectURL(url);
                    resolve();
                };
                chatAudioRef.current.onerror = () => {
                    URL.revokeObjectURL(url);
                    resolve();
                };
                chatAudioRef.current.play().catch(() => resolve());
            });
        });
    }, [isLocalhost, selectedVoice, playbackSpeed, volume, requestTimeout, synthesizeText]);

    const stopChatPlayback = useCallback(() => {
        chatAudioRef.current.pause();
        chatAudioRef.current.currentTime = 0;
        window.speechSynthesis.cancel();
    }, []);

    // Play an already-fetched audio blob URL on the chat audio channel; resolves on end/error.
    // Used by the chat playback loop so synthesis can be prefetched (mirrors reader's pattern).
    const playChatUrl = useCallback((url) => {
        return new Promise((resolve) => {
            if (!url) { resolve(); return; }
            chatAudioRef.current.volume = volume;
            chatAudioRef.current.src = url;
            chatAudioRef.current.onended = () => resolve();
            chatAudioRef.current.onerror = () => resolve();
            chatAudioRef.current.play().catch(() => resolve());
        });
    }, [volume]);

    // Play a fallback (Web Speech API) utterance; resolves on end/error.
    const playChatSpeech = useCallback((text) => {
        return new Promise((resolve) => {
            if (!text || !text.trim()) { resolve(); return; }
            const ut = new SpeechSynthesisUtterance(text);
            ut.rate = playbackSpeed;
            ut.volume = volume;
            ut.onend = () => resolve();
            ut.onerror = () => resolve();
            window.speechSynthesis.speak(ut);
        });
    }, [playbackSpeed, volume]);

    // --- VOLUME CONTROL ---
    useEffect(() => {
        audioRef.current.volume = volume;
    }, [volume]);

    // --- CACHE MANAGEMENT ---
    const clearCache = useCallback(() => {
        audioCache.current.forEach(url => URL.revokeObjectURL(url));
        audioCache.current.clear();
        pendingRequests.current.clear();
    }, []);

    // Clear cache when page content changes (new textItems = new page)
    useEffect(() => {
        clearCache();
    }, [textItems, clearCache]);

    const fetchAudio = async (index) => {
        if (index < 0 || index >= textItems.length) return null;
        if (audioCache.current.has(index)) return audioCache.current.get(index);

        // If there's already a request in flight for this index, wait for it
        if (pendingRequests.current.has(index)) {
            return pendingRequests.current.get(index);
        }

        const fetchPromise = (async () => {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);
            try {
                const url = await synthesizeText(textItems[index], {
                    voice: selectedVoice,
                    speed: playbackSpeed,
                    signal: controller.signal,
                });
                if (url) audioCache.current.set(index, url);
                return url;
            } finally {
                clearTimeout(timeoutId);
                pendingRequests.current.delete(index);
            }
        })();

        pendingRequests.current.set(index, fetchPromise);
        return fetchPromise;
    };

    const prefetchBuffer = useCallback(async (currentIndex) => {
        if (!isLocalhost) return;
        for (let i = 1; i <= 2; i++) {
            const target = currentIndex + i;
            if (target < textItems.length && !audioCache.current.has(target)) {
                fetchAudio(target);
            }
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [textItems, selectedVoice, playbackSpeed, isLocalhost]);

    // --- PLAYBACK CONTROLS ---
    const handlePlayPause = () => {
        if (isPlaying) {
            setIsPlaying(false);
            audioRef.current.pause();
            window.speechSynthesis.cancel();
        } else {
            setIsPlaying(true);
            retryCountRef.current = 0;
        }
    };

    const stopPlayback = () => {
        setIsPlaying(false);
        playbackIndexRef.current = -1;
        setCurrentSentenceIndex(-1);
        audioRef.current.pause();
        window.speechSynthesis.cancel();
        setStatus("Playback Stopped");
    };

    const skipToNextSentence = () => {
        if (currentSentenceIndex < textItems.length - 1) {
            audioRef.current.pause();
            window.speechSynthesis.cancel();
            setCurrentSentenceIndex(prev => prev + 1);
        }
    };

    // Stop reader playback when the engine is disabled (e.g. switching to chat view)
    useEffect(() => {
        if (!enabled && isPlaying) {
            setIsPlaying(false);
            audioRef.current.pause();
            window.speechSynthesis.cancel();
        }
    }, [enabled, isPlaying]);

    // --- MAIN PLAYBACK LOOP ---
    useEffect(() => {
        if (!enabled || !isPlaying) return;

        let active = true;

        const playLoop = async () => {
            const nextIdx = playbackIndexRef.current + 1;

            if (nextIdx >= textItems.length) {
                if (currentPage < numPages) {
                    setStatus("Changing Page...");
                    // Stop any ongoing audio so the guard condition passes
                    // when the effect re-runs with new textItems
                    audioRef.current.pause();
                    audioRef.current.currentTime = 0;
                    window.speechSynthesis.cancel();
                    setCurrentPage(p => p + 1);
                    playbackIndexRef.current = -1;
                    setCurrentSentenceIndex(-1);
                    retryCountRef.current = 0;
                } else {
                    stopPlayback();
                    setStatus("End of Document");
                }
                return;
            }

            const textToRead = textItems[nextIdx];
            prefetchBuffer(nextIdx);

            if (isLocalhost) {
                setStatus("Generating Voice...");
                const url = await fetchAudio(nextIdx);
                if (!active) return;

                // Commit position only after confirming effect is still active
                // (prevents StrictMode double-run from skipping sentences)
                playbackIndexRef.current = nextIdx;
                setCurrentSentenceIndex(nextIdx);

                if (url) {
                    retryCountRef.current = 0;
                    setStatus("Reading...");
                    audioRef.current.src = url;
                    audioRef.current.onended = () => {
                        URL.revokeObjectURL(url);
                        audioCache.current.delete(nextIdx);
                        if (active) playLoop();
                    };
                    audioRef.current.play().catch(e => {
                        console.error("Audio block", e);
                        setStatus("Wait for interaction...");
                    });
                } else {
                    retryCountRef.current += 1;
                    if (retryCountRef.current >= 3) {
                        stopPlayback();
                        setStatus("Connection failed");
                        showToast('Server unreachable after 3 attempts. Check your connection and try again.', 6000);
                        retryCountRef.current = 0;
                    } else {
                        setStatus(`Connection Error — Retry ${retryCountRef.current}/3...`);
                        setTimeout(() => {
                            if (active) playLoop();
                        }, 2000);
                    }
                }
            } else {
                // System voice — commit position immediately (no async gap)
                playbackIndexRef.current = nextIdx;
                setCurrentSentenceIndex(nextIdx);
                setStatus("Using System Voice...");
                const ut = new SpeechSynthesisUtterance(textToRead);
                ut.rate = playbackSpeed;
                ut.onend = () => {
                    if (active) playLoop();
                };
                window.speechSynthesis.speak(ut);
            }
        };

        if (textItems.length > 0 && audioRef.current.paused && !window.speechSynthesis.speaking) {
            playLoop();
        }

        return () => { active = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isPlaying, textItems, enabled]);

    // --- SELECTION READING ---
    const readSelection = async () => {
        const selection = window.getSelection().toString().trim();
        if (!selection) {
            showToast('Select some text first', 3000);
            return;
        }

        setIsReadingSelection(true);
        setStatus("Reading selection...");

        if (isLocalhost) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);
            try {
                const response = await fetch(getApiUrl('/v1/synthesize'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        text: selection,
                        voice: selectedVoice,
                        speed: playbackSpeed
                    }),
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                if (!response.ok) throw new Error("TTS failed");
                const data = await response.json();
                const b64 = data.audio_base64;
                const blob = await (await fetch(`data:audio/wav;base64,${b64}`)).blob();
                const url = URL.createObjectURL(blob);

                audioRef.current.src = url;
                audioRef.current.onended = () => {
                    setIsReadingSelection(false);
                    setStatus("Selection read complete");
                    URL.revokeObjectURL(url);
                };
                audioRef.current.play();
            } catch (e) {
                clearTimeout(timeoutId);
                console.error("Selection read error:", e);
                setIsReadingSelection(false);
                setStatus(e.name === 'AbortError' ? "Selection read timed out" : "Failed to read selection");
            }
        } else {
            const utterance = new SpeechSynthesisUtterance(selection);
            utterance.rate = playbackSpeed;
            utterance.onend = () => {
                setIsReadingSelection(false);
                setStatus("Selection read complete");
            };
            window.speechSynthesis.speak(utterance);
        }
    };

    const stopSelectionRead = () => {
        audioRef.current.pause();
        window.speechSynthesis.cancel();
        setIsReadingSelection(false);
        setStatus("Selection stopped");
    };

    // --- VOICE PREVIEW ---
    const previewVoice = async (voiceId) => {
        const voice = KOKORO_VOICES.find(v => v.id === voiceId);
        if (!voice || !voice.sampleText) return;

        voicePreviewRef.current.pause();
        window.speechSynthesis.cancel();
        setIsPreviewingVoice(true);
        setStatus(`Previewing ${voice.name}...`);

        if (isLocalhost && backendAvailable) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);
            try {
                const response = await fetch(getApiUrl('/v1/synthesize'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        text: voice.sampleText,
                        voice: voiceId,
                        speed: playbackSpeed
                    }),
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                if (!response.ok) throw new Error("TTS failed");
                const data = await response.json();
                const b64 = data.audio_base64;
                const blob = await (await fetch(`data:audio/wav;base64,${b64}`)).blob();
                const url = URL.createObjectURL(blob);

                voicePreviewRef.current.src = url;
                voicePreviewRef.current.volume = volume;
                voicePreviewRef.current.onended = () => {
                    setIsPreviewingVoice(false);
                    setStatus("Preview complete");
                    URL.revokeObjectURL(url);
                };
                voicePreviewRef.current.play();
            } catch (e) {
                clearTimeout(timeoutId);
                console.error("Voice preview error:", e);
                setIsPreviewingVoice(false);
                setStatus(e.name === 'AbortError' ? "Preview timed out" : "Preview failed");
            }
        } else {
            const utterance = new SpeechSynthesisUtterance(voice.sampleText);
            utterance.rate = playbackSpeed;
            utterance.onend = () => {
                setIsPreviewingVoice(false);
                setStatus("Preview complete");
            };
            window.speechSynthesis.speak(utterance);
        }
    };

    const stopVoicePreview = () => {
        voicePreviewRef.current.pause();
        window.speechSynthesis.cancel();
        setIsPreviewingVoice(false);
        setStatus("Preview stopped");
    };

    // --- DOWNLOAD PAGE AUDIO ---
    const downloadPageAudio = async () => {
        if (!textItems.length || !isLocalhost) {
            setStatus(isLocalhost ? "No text to download" : "Download requires Kokoro backend");
            return;
        }

        setIsDownloading(true);
        setStatus("Generating audio for page...");

        const controller = new AbortController();
        let timeoutId = null;
        if (!unlimitedBatchTimeout) {
            timeoutId = setTimeout(() => controller.abort(), requestTimeout * 4 * 1000);
        }

        try {
            const response = await fetch(getApiUrl('/v1/batch_synthesize'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sentences: textItems,
                    voice: selectedVoice,
                    speed: playbackSpeed
                }),
                signal: unlimitedBatchTimeout ? undefined : controller.signal
            });

            clearTimeout(timeoutId);

            if (!response.ok) throw new Error("Batch synthesis failed");

            const data = await response.json();
            const b64 = data.audio_base64;

            const byteCharacters = atob(b64);
            const byteNumbers = new Array(byteCharacters.length);
            for (let i = 0; i < byteCharacters.length; i++) {
                byteNumbers[i] = byteCharacters.charCodeAt(i);
            }
            const byteArray = new Uint8Array(byteNumbers);
            const blob = new Blob([byteArray], { type: 'audio/wav' });

            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${pdfFileName.replace(/\.(pdf|txt)$/i, '')}_page${currentPage}.wav`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            setStatus(`Downloaded page ${currentPage} audio (${Math.round(data.duration_seconds)}s)`);
        } catch (err) {
            clearTimeout(timeoutId);
            console.error("Download error:", err);
            setStatus(err.name === 'AbortError' ? "Download timed out" : "Failed to generate audio");
        } finally {
            setIsDownloading(false);
        }
    };

    // Generic text → .wav download. Used by the chat view to export an
    // assistant message's audio. Strips markdown via the same helper the
    // chat-playback path uses so the spoken output matches what's read aloud,
    // and splits into sentences so kokoro's batch endpoint can chunk them.
    //
    // `trackingId` is opaque — when provided we flip `downloadingMessageId`
    // to it so the originating UI can show a per-item spinner. The page-audio
    // download keeps using `isDownloading` separately.
    const downloadTextAudio = useCallback(async (text, filenameBase = 'audio', trackingId = null) => {
        if (!isLocalhost) {
            showToast?.('Audio download requires Kokoro backend (system voice has no file).', 4000);
            return false;
        }
        const cleaned = markdownToSpeech(text || '').trim();
        if (!cleaned) {
            showToast?.('Nothing to synthesize.', 3000);
            return false;
        }
        const sentences = cleaned
            .replace(/\s+/g, ' ')
            .split(/(?<=[.!?])\s+/)
            .map(s => s.trim())
            .filter(s => s.length > 0);
        if (sentences.length === 0) {
            showToast?.('Nothing to synthesize.', 3000);
            return false;
        }

        if (trackingId) setDownloadingMessageId(trackingId);
        const controller = new AbortController();
        const timeoutId = unlimitedBatchTimeout
            ? null
            : setTimeout(() => controller.abort(), requestTimeout * 4 * 1000);
        try {
            const response = await fetch(buildApiUrl(apiHost, apiPort, '/v1/batch_synthesize'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sentences,
                    voice: selectedVoice,
                    speed: playbackSpeed,
                }),
                signal: unlimitedBatchTimeout ? undefined : controller.signal,
            });
            if (timeoutId) clearTimeout(timeoutId);
            if (!response.ok) throw new Error('Batch synthesis failed');

            const data = await response.json();
            const b64 = data.audio_base64;
            const byteCharacters = atob(b64);
            const byteNumbers = new Array(byteCharacters.length);
            for (let i = 0; i < byteCharacters.length; i++) {
                byteNumbers[i] = byteCharacters.charCodeAt(i);
            }
            const blob = new Blob([new Uint8Array(byteNumbers)], { type: 'audio/wav' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            // Keep the filename safe across OSes — strip path separators and
            // anything that tends to confuse Windows in particular.
            const safe = String(filenameBase).replace(/[\\/:*?"<>|]/g, '_').slice(0, 80) || 'audio';
            a.download = `${safe}.wav`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 0);
            return true;
        } catch (err) {
            if (timeoutId) clearTimeout(timeoutId);
            console.error('Text audio download error:', err);
            showToast?.(
                err.name === 'AbortError' ? 'Download timed out.' : 'Failed to generate audio.',
                5000,
            );
            return false;
        } finally {
            if (trackingId) setDownloadingMessageId(null);
        }
    }, [isLocalhost, unlimitedBatchTimeout, requestTimeout, apiHost, apiPort, selectedVoice, playbackSpeed, showToast]);

    // Synthesize an entire document (PDF / TXT / MD) and download it as a
    // single WAV. Operates page-by-page so progress is visible and a single
    // bad page can't tank the whole job — failed pages are skipped with a
    // toast warning rather than aborting.
    //
    // `pages` shape: [{ page: number, text: string }, ...]  (one entry per page)
    // `filenameBase`: name of the output file (extension is added).
    const downloadBookAudio = useCallback(async (pages, filenameBase = 'audiobook') => {
        if (!isLocalhost) {
            showToast?.('Audiobook export requires the Kokoro backend.', 5000);
            return false;
        }
        if (!Array.isArray(pages) || pages.length === 0) {
            showToast?.('Nothing to synthesize.', 3000);
            return false;
        }

        // Re-entrancy guard — if a download is already in flight, surface the
        // existing one rather than starting a duplicate.
        if (bookAbortRef.current) {
            showToast?.('An audiobook is already being generated.', 4000);
            return false;
        }

        const controller = new AbortController();
        bookAbortRef.current = controller;
        setBookProgress({ current: 0, total: pages.length, label: 'Preparing…' });

        const wavBlobs = [];
        let failed = 0;
        try {
            for (let i = 0; i < pages.length; i++) {
                if (controller.signal.aborted) break;
                const { page, text } = pages[i] || {};
                setBookProgress({ current: i, total: pages.length, label: `Synthesizing page ${page ?? i + 1}…` });

                const cleaned = (text || '').trim();
                if (!cleaned) continue;
                const sentences = cleaned
                    .replace(/\s+/g, ' ')
                    .split(/(?<=[.!?])\s+/)
                    .map((s) => s.trim())
                    .filter((s) => s.length > 0);
                if (sentences.length === 0) continue;

                try {
                    const response = await fetch(buildApiUrl(apiHost, apiPort, '/v1/batch_synthesize'), {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            sentences,
                            voice: selectedVoice,
                            speed: playbackSpeed,
                        }),
                        // Audiobook export deliberately ignores `unlimitedBatchTimeout`
                        // for the *per-page* call — each page is bounded; only the
                        // outer loop is unbounded. The user can cancel any time.
                        signal: controller.signal,
                    });
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const data = await response.json();
                    const b64 = data.audio_base64;
                    if (!b64) throw new Error('No audio in response');
                    const byteCharacters = atob(b64);
                    const byteNumbers = new Uint8Array(byteCharacters.length);
                    for (let k = 0; k < byteCharacters.length; k++) {
                        byteNumbers[k] = byteCharacters.charCodeAt(k);
                    }
                    wavBlobs.push(new Blob([byteNumbers], { type: 'audio/wav' }));
                } catch (e) {
                    if (e.name === 'AbortError') throw e;
                    console.warn(`Page ${page ?? i + 1} failed to synthesize:`, e);
                    failed += 1;
                }
            }

            if (controller.signal.aborted) {
                setStatus?.('Audiobook export cancelled');
                return false;
            }
            if (wavBlobs.length === 0) {
                showToast?.('No pages could be synthesized.', 5000);
                return false;
            }

            setBookProgress({ current: pages.length, total: pages.length, label: 'Stitching audio…' });
            const merged = await concatWavs(wavBlobs);
            if (!merged) {
                showToast?.('Could not stitch the audio together.', 5000);
                return false;
            }

            const url = URL.createObjectURL(merged);
            const safe = String(filenameBase).replace(/[\\/:*?"<>|]/g, '_').slice(0, 80) || 'audiobook';
            const a = document.createElement('a');
            a.href = url;
            a.download = `${safe}.wav`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 0);

            if (failed > 0) {
                showToast?.(`Audiobook ready — ${failed} page(s) failed and were skipped.`, 6000);
            } else {
                setStatus?.(`Audiobook downloaded (${wavBlobs.length} page${wavBlobs.length === 1 ? '' : 's'})`);
            }
            return true;
        } catch (e) {
            if (e.name === 'AbortError') {
                setStatus?.('Audiobook export cancelled');
                return false;
            }
            console.error('Audiobook export failed:', e);
            showToast?.(`Audiobook export failed: ${e.message}`, 6000);
            return false;
        } finally {
            bookAbortRef.current = null;
            setBookProgress(null);
        }
    }, [isLocalhost, apiHost, apiPort, selectedVoice, playbackSpeed, showToast, setStatus]);

    const cancelBookDownload = useCallback(() => {
        bookAbortRef.current?.abort();
    }, []);

    return {
        // State
        isPlaying, setIsPlaying,
        isDownloading,
        downloadingMessageId,
        bookProgress,
        isReadingSelection,
        isPreviewingVoice,

        // Actions
        handlePlayPause,
        stopPlayback,
        skipToNextSentence,
        readSelection,
        stopSelectionRead,
        previewVoice,
        stopVoicePreview,
        downloadPageAudio,
        downloadTextAudio,
        downloadBookAudio,
        cancelBookDownload,
        clearCache,

        // Reusable helpers (used by useChatEngine for AI response playback)
        synthesizeText,
        playSentence,        // legacy synth+play (kept for any callers that want both in one)
        playChatUrl,         // play a pre-fetched blob URL on the chat audio channel
        playChatSpeech,      // Web Speech API fallback for !isLocalhost
        stopChatPlayback,
        isLocalhost,
        selectedVoice,
        playbackSpeed,
        requestTimeout,
    };
}
