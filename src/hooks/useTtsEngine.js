import { useState, useEffect, useRef, useCallback } from 'react';
import { KOKORO_VOICES } from '../constants';

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
    setStatus,
    setToastMessage,
}) {
    const [isPlaying, setIsPlaying] = useState(false);
    const [isDownloading, setIsDownloading] = useState(false);
    const [isReadingSelection, setIsReadingSelection] = useState(false);
    const [isPreviewingVoice, setIsPreviewingVoice] = useState(false);

    const audioCache = useRef(new Map());
    const audioRef = useRef(new Audio());
    const voicePreviewRef = useRef(new Audio());
    const retryCountRef = useRef(0);

    // Helper to build API URL
    const getApiUrl = (endpoint) => `http://${apiHost}:${apiPort}${endpoint}`;

    // --- VOLUME CONTROL ---
    useEffect(() => {
        audioRef.current.volume = volume;
    }, [volume]);

    // --- CACHE MANAGEMENT ---
    const clearCache = useCallback(() => {
        audioCache.current.forEach(url => URL.revokeObjectURL(url));
        audioCache.current.clear();
    }, []);

    const fetchAudio = async (index) => {
        if (index < 0 || index >= textItems.length) return null;
        if (audioCache.current.has(index)) return audioCache.current.get(index);

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);

        try {
            const response = await fetch(getApiUrl('/v1/synthesize'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: textItems[index],
                    voice: selectedVoice,
                    speed: playbackSpeed
                }),
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            if (!response.ok) throw new Error("TTS Fail");
            const data = await response.json();

            const b64 = data.audio_base64 || data.audio;
            const blob = await (await fetch(`data:audio/wav;base64,${b64}`)).blob();
            const url = URL.createObjectURL(blob);
            audioCache.current.set(index, url);
            return url;
        } catch (err) {
            clearTimeout(timeoutId);
            console.error("Inference Error:", err);
            return null;
        }
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

    // --- MAIN PLAYBACK LOOP ---
    useEffect(() => {
        if (!isPlaying) return;

        let active = true;

        const playLoop = async () => {
            const nextIdx = playbackIndexRef.current + 1;

            if (nextIdx >= textItems.length) {
                if (currentPage < numPages) {
                    setStatus("Changing Page...");
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
            playbackIndexRef.current = nextIdx;
            setCurrentSentenceIndex(nextIdx);
            prefetchBuffer(nextIdx);

            if (isLocalhost) {
                setStatus("Generating Voice...");
                const url = await fetchAudio(nextIdx);
                if (!active) return;

                if (url) {
                    retryCountRef.current = 0;
                    setStatus("Reading...");
                    audioRef.current.src = url;
                    audioRef.current.onended = () => {
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
                        setToastMessage("Server unreachable after 3 attempts. Check your connection and try again.");
                        setTimeout(() => setToastMessage(null), 6000);
                        retryCountRef.current = 0;
                    } else {
                        setStatus(`Connection Error — Retry ${retryCountRef.current}/3...`);
                        setTimeout(() => {
                            if (active) playLoop();
                        }, 2000);
                    }
                }
            } else {
                setStatus("Using System Voice...");
                const ut = new SpeechSynthesisUtterance(textToRead);
                ut.rate = playbackSpeed;
                ut.onend = () => {
                    if (active) playLoop();
                };
                window.speechSynthesis.speak(ut);
            }
        };

        if (audioRef.current.paused && !window.speechSynthesis.speaking) {
            playLoop();
        }

        return () => { active = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isPlaying, textItems, currentPage]);

    // --- SELECTION READING ---
    const readSelection = async () => {
        const selection = window.getSelection().toString().trim();
        if (!selection) {
            setToastMessage("Select some text first");
            setTimeout(() => setToastMessage(null), 3000);
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
            a.download = `${pdfFileName.replace('.pdf', '')}_page${currentPage}.wav`;
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

    return {
        // State
        isPlaying, setIsPlaying,
        isDownloading,
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
        clearCache,
    };
}
