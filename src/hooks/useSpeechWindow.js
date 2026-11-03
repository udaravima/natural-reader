import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../utils/apiFetch';
import { refusalMessage } from '../lib/apiErrors';
import { createConversation } from '../lib/speech/conversation';
import { listen } from '../lib/speech/listen';
import { encodeWav } from '../lib/speech/wav';
import { useLatest } from './useLatest';

export const MIC_BLOCKED = "Microphone blocked: allow it in the browser's site settings";
const CLIP_RATE = 16000;   // what the VAD hands over
const CLIP_FAILED = "The transcription server didn't answer";
const READY_POLL_MS = 3000;   // how often to ask while the speech model loads

/** /v1/speech/status, for a person who could use it: null until it answers, or if it doesn't. */
export function useSpeechStatus({ apiHost, apiPort, allowed }) {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    if (!allowed) return undefined;
    let current = true;
    let timer;
    const read = async () => {
      let body = null;
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/speech/status');
        if (res.ok) body = await res.json();
      } catch { /* no answer is the answer */ }
      if (!current) return;
      setStatus(body);
      if (body?.enabled && !body.ready) timer = setTimeout(read, READY_POLL_MS);
    };
    read();
    return () => { current = false; clearTimeout(timer); };
  }, [apiHost, apiPort, allowed]);
  return status;
}

/**
 * The speech window's link to the world: the microphone, Whisper and the chat, around the conversation in
 * lib/speech/conversation.js. As with useVoiceInput, the microphone is released on every way out, a stream that
 * arrives after the person left included: a recording light left on is a privacy failure.
 */
export function useSpeechWindow(props) {
  const [phase, setPhase] = useState('off');
  const [clips, setClips] = useState([]);
  const [muted, setMuted] = useState(false);
  const run = useRef(null);      // { conversation, mic, audio } while it's on
  const session = useRef(0);     // bumped by every start and end: a late microphone sees a new number
  const awake = useRef(null);    // the screen's wake lock while talking: a phone that sleeps stops listening
  const latest = useLatest(props);

  const noteClip = useCallback((clip) => {
    setClips((all) => {
      const copy = { ...clip };
      return all.some((c) => c.id === clip.id) ? all.map((c) => (c.id === clip.id ? copy : c)) : [...all, copy];
    });
  }, []);

  const transcribe = useCallback(async (samples) => {
    const { apiHost, apiPort, settings } = latest.current;
    const form = new FormData();
    form.append('file', encodeWav(samples, CLIP_RATE), 'clip.wav');
    if (settings.language) form.append('language', settings.language);
    const res = await apiFetch(apiHost, apiPort, '/v1/speech/transcriptions', { method: 'POST', body: form });
    if (!res.ok) throw new Error((await refusalMessage(res)) ?? CLIP_FAILED);
    return res.json();
  }, [latest]);

  const synthesize = useCallback((text, signal) => {
    const { synthesizeText, voice, speed, requestTimeout } = latest.current;
    return synthesizeText(text, {
      voice, speed, signal: AbortSignal.any([signal, AbortSignal.timeout(requestTimeout * 1000)]),
    });
  }, [latest]);

  const close = useCallback(() => {
    session.current += 1;
    const current = run.current;
    run.current = null;
    current?.conversation.end();
    current?.mic.destroy();
    awake.current?.release().catch(() => {});
    awake.current = null;
  }, []);

  // A browser without wake locks, or one that refuses (a phone saving its battery), talks all the same.
  const keepAwake = useCallback(async () => {
    const mine = session.current;
    let lock;
    try { lock = await navigator.wakeLock?.request('screen'); } catch { return; }
    if (!lock) return;
    if (mine !== session.current) { lock.release().catch(() => {}); return; }   // ended meanwhile
    awake.current = lock;
  }, []);

  const start = useCallback(async () => {
    if (run.current) return;
    session.current += 1;
    const mine = session.current;
    const { settings, showToast, onStarted } = latest.current;
    setPhase('starting');
    setClips([]);
    const audio = new Audio();
    audio.volume = latest.current.volume;
    const conversation = createConversation({
      settings,
      transcribe,
      synthesize,
      audio,
      send: (text, { heard, onStart, onText }) => latest.current.sendMessage(text, [], {
        spoken: { onStart, onText }, ...(heard ? { heard } : {}),
      }),
      abort: () => latest.current.stopStream(),
      onChange: setPhase,
      onClip: noteClip,
    });
    let mic;
    try {
      mic = await listen({
        settings,
        onSpeechStarted: conversation.speechStarted,
        onSpeechFailed: conversation.speechFailed,
        onSpeechEnded: conversation.speechEnded,
        onLevel: (level) => latest.current.onLevel?.(level),
      });
    } catch (e) {
      if (mine !== session.current) return;
      setPhase('off');
      showToast?.(e?.name === 'NotAllowedError' ? MIC_BLOCKED : "The microphone couldn't start.", 5000);
      return;
    }
    if (mine !== session.current) { mic.destroy(); return; }   // gave up meanwhile
    run.current = { conversation, mic, audio };
    onStarted?.();
    conversation.begin();
    keepAwake();
  }, [latest, transcribe, synthesize, noteClip, keepAwake]);

  const end = useCallback(() => {
    close();
    setPhase('off');
    setMuted(false);
  }, [close]);

  const toggleMute = useCallback(async () => {
    const mic = run.current?.mic;
    if (!mic) return;
    if (muted) await mic.start(); else await mic.pause();
    setMuted(!muted);
  }, [muted]);

  const cutIn = useCallback(() => { run.current?.conversation.cutIn(); }, []);

  // Hiding the page lets the screen sleep again: ask again when it comes back.
  useEffect(() => {
    const again = () => { if (run.current && document.visibilityState === 'visible') keepAwake(); };
    document.addEventListener('visibilitychange', again);
    return () => document.removeEventListener('visibilitychange', again);
  }, [keepAwake]);

  useEffect(() => close, [close]);

  return { phase, clips, muted, start, end, toggleMute, cutIn };
}
