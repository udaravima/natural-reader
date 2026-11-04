import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../utils/apiFetch';

/**
 * Speak a message (chat composer inputs, spec §4.1): record from the microphone, send the recording to
 * POST /v1/speech/transcriptions, and hand the text to `onText(text, language)`. The person edits it and
 * presses Send; nothing here sends a chat message.
 *
 * Three things matter more than the happy path:
 *  - The microphone is released on EVERY way out (Stop, Cancel, the 2-minute stop, Escape, a recorder error,
 *    unmount, and a stream that arrives after the person already gave up). A recording light left on after the
 *    person thinks they've stopped is a privacy failure.
 *  - One thing at a time. `phaseRef` is a ref, not state, so two presses in one tick still make one recorder.
 *  - An answer that arrives after Cancel or unmount changes nothing: every await re-checks `sessionRef`.
 */

export const REASON_INSECURE = 'Voice input needs a secure (https) connection';
const REASON_NO_RECORDER = "This browser can't record audio";
export const REASON_BLOCKED = "Microphone blocked: allow it in the browser's site settings";
// The two reasons that can't change without a reload: the button is truly disabled for them. A blocked
// microphone can be allowed in the browser's site settings, so that button stays pressable (it asks again).
export const HARD_REASONS = [REASON_INSECURE, REASON_NO_RECORDER];

const PREFERRED_MIME = 'audio/webm;codecs=opus';
const MAX_SECONDS = 120;
const FALLBACK = "The transcription server didn't answer";

function fileName(mime) {
  const m = (mime || '').toLowerCase();
  if (m.includes('ogg')) return 'recording.ogg';
  if (m.includes('mp4') || m.includes('m4a') || m.includes('aac')) return 'recording.m4a';
  return 'recording.webm';
}

function stopTracks(stream) {
  try { stream?.getTracks().forEach((t) => t.stop()); } catch { /* already gone */ }
}

async function detailOf(res) {
  try {
    const body = await res.json();
    // The server's refusals send {detail: {error, message}}; an older route sends a plain string.
    const message = body?.detail?.message;
    if (typeof message === 'string' && message) return message;
    if (typeof body?.detail === 'string' && body.detail) return body.detail;
  } catch { /* not JSON */ }
  return FALLBACK;
}

export function useVoiceInput({ apiHost, apiPort, language = 'auto', onText, showToast } = {}) {
  const [status, setStatus] = useState(null);   // the last /v1/speech/status answer; null: none or failed
  const [phase, setPhaseState] = useState('idle');
  const [seconds, setSeconds] = useState(0);
  const [blocked, setBlocked] = useState(false);

  const phaseRef = useRef('idle');     // 'idle' | 'starting' | 'recording' | 'transcribing'
  const sessionRef = useRef(0);        // bumped by every start and every cancel: a stale answer sees a new number
  const mountedRef = useRef(false);
  const streamRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const tickRef = useRef(null);
  const abortRef = useRef(null);
  const startedAtRef = useRef(0);   // wall clock (ms) of the recording's start: timers run late when throttled
  // The latest of everything an async step reads after its awaits.
  const latest = useRef({});
  latest.current = { language, onText, showToast, status, apiHost, apiPort };

  const setPhase = (p) => { phaseRef.current = p; if (mountedRef.current) setPhaseState(p); };
  const toast = (msg, ms = 4000) => { if (mountedRef.current) latest.current.showToast?.(msg, ms); };

  const clearTick = () => { if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; } };
  const release = () => { stopTracks(streamRef.current); streamRef.current = null; };

  const loadStatus = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/speech/status');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      if (mountedRef.current) setStatus(body);
      return body?.enabled === true;
    } catch {
      if (mountedRef.current) setStatus(null);
      return false;
    }
  }, [apiHost, apiPort]);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  // Drop everything in flight. Used by Cancel, Escape, a recorder error and unmount.
  const teardown = useCallback(() => {
    sessionRef.current += 1;
    clearTick();
    const rec = recorderRef.current;
    recorderRef.current = null;
    if (rec) {
      rec.onstop = null; rec.ondataavailable = null; rec.onerror = null;
      try { if (rec.state !== 'inactive') rec.stop(); } catch { /* noop */ }
    }
    chunksRef.current = [];
    release();
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const cancel = useCallback(() => {
    teardown();
    setPhase('idle');
  }, [teardown]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      // Leaving the chat view (a tab switch) throws a recording or a transcription away. `toast` does nothing
      // once unmounted, so say it directly: somebody who pressed Stop after two minutes must not come back to
      // nothing and wonder.
      const inFlight = phaseRef.current === 'recording' || phaseRef.current === 'transcribing';
      mountedRef.current = false;
      teardown();
      phaseRef.current = 'idle';
      if (inFlight) latest.current.showToast?.('Your recording was discarded: you left the chat.', 5000);
    };
  }, [teardown]);

  const upload = async (blob, session) => {
    const controller = new AbortController();
    abortRef.current = controller;
    const fd = new FormData();
    fd.append('file', blob, fileName(blob.type));
    const { language: lang, status: st } = latest.current;
    if (lang && lang !== 'auto' && st?.languages?.some((l) => l.code === lang)) fd.append('language', lang);
    try {
      const res = await apiFetch(latest.current.apiHost, latest.current.apiPort, '/v1/speech/transcriptions', {
        method: 'POST', body: fd, signal: controller.signal,
      });
      if (session !== sessionRef.current) return;
      if (res.status === 404) {
        // Voice was switched off (the button goes away, and the person is told: they may have just spoken for a
        // minute). If it is still on, the 404 is a mystery: say so.
        const stillOn = await loadStatus();
        if (session === sessionRef.current) {
          toast(stillOn ? "Voice input isn't available right now." : 'Voice input was turned off.', 5000);
        }
        return;
      }
      if (!res.ok) { toast(await detailOf(res), 5000); return; }
      const out = await res.json();
      if (session !== sessionRef.current || controller.signal.aborted) return;
      const text = typeof out?.text === 'string' ? out.text.trim() : '';
      if (!text) { toast('No speech was heard'); return; }
      latest.current.onText?.(text, out.language);
    } catch (e) {
      if (e?.name === 'AbortError' || session !== sessionRef.current) return;
      toast(FALLBACK, 5000);
    } finally {
      if (session === sessionRef.current) {
        abortRef.current = null;
        setPhase('idle');
      }
    }
  };

  const stop = useCallback(() => {
    if (phaseRef.current !== 'recording') return;
    const rec = recorderRef.current;
    const session = sessionRef.current;
    setPhase('transcribing');
    clearTick();
    if (!rec) { cancel(); return; }
    // The recorder's last chunk and its stop event arrive after stop(): collect them, then upload.
    let finished = false;
    const finish = () => {
      if (finished || session !== sessionRef.current) return;
      finished = true;
      const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' });
      chunksRef.current = [];
      recorderRef.current = null;
      if (blob.size === 0) {
        toast('No speech was heard');
        setPhase('idle');
        return;
      }
      upload(blob, session);
    };
    if (rec.state === 'inactive') {
      // The browser already stopped it (a microphone unplugged): no stop event will come, and stop() would throw.
      release();
      finish();
      return;
    }
    rec.onstop = finish;
    try { rec.stop(); } catch { finish(); }
    release();   // the light goes off now, not when the upload ends
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cancel]);

  const start = useCallback(async () => {
    if (phaseRef.current !== 'idle') return;
    phaseRef.current = 'starting';
    const session = ++sessionRef.current;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      if (session !== sessionRef.current) return;
      setPhase('idle');
      if (e?.name === 'NotAllowedError') {
        if (mountedRef.current) setBlocked(true);
        toast(REASON_BLOCKED, 5000);
      } else if (e?.name === 'NotFoundError') toast('No microphone was found.', 5000);
      else toast("The microphone couldn't start.", 5000);
      return;
    }
    if (session !== sessionRef.current || !mountedRef.current) { stopTracks(stream); return; }   // gave up meanwhile
    streamRef.current = stream;
    if (mountedRef.current) setBlocked(false);
    let recorder;
    try {
      recorder = MediaRecorder.isTypeSupported?.(PREFERRED_MIME)
        ? new MediaRecorder(stream, { mimeType: PREFERRED_MIME })
        : new MediaRecorder(stream);
    } catch {
      release();
      setPhase('idle');
      toast('The recording failed.', 5000);
      return;
    }
    chunksRef.current = [];
    recorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunksRef.current.push(e.data); };
    recorder.onerror = () => {
      if (session !== sessionRef.current) return;
      teardown();
      setPhase('idle');
      toast('The recording failed.', 5000);
    };
    recorderRef.current = recorder;
    try {
      recorder.start();
    } catch {
      teardown();
      setPhase('idle');
      toast('The recording failed.', 5000);
      return;
    }
    startedAtRef.current = Date.now();
    setSeconds(0);
    setPhase('recording');
    tickRef.current = setInterval(() => {
      // From the wall clock, not a tick count: a throttled tab runs ticks late, and the server refuses past 125 s.
      const elapsed = Math.max(0, Math.floor((Date.now() - startedAtRef.current) / 1000));
      if (mountedRef.current) setSeconds(Math.min(elapsed, MAX_SECONDS));
      if (elapsed >= MAX_SECONDS) stop();
    }, 1000);
  }, [stop, teardown]);

  // Escape cancels while recording or transcribing, and only then.
  useEffect(() => {
    if (phase !== 'recording' && phase !== 'transcribing') return undefined;
    const onKey = (e) => { if (e.key === 'Escape') cancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [phase, cancel]);

  const available = status?.enabled === true;
  let reason = null;
  if (typeof window !== 'undefined' && window.isSecureContext === false) reason = REASON_INSECURE;
  else if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) reason = REASON_NO_RECORDER;
  else if (blocked) reason = REASON_BLOCKED;

  // `status`: the last /v1/speech/status answer, for the Speaking switch beside the microphone.
  return { available, reason, phase, seconds, start, stop, cancel, status };
}
