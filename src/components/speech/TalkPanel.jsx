import { useEffect, useRef } from 'react';
import { AudioLines, Loader2, Mic, MicOff, PhoneOff, SlidersHorizontal } from 'lucide-react';
import { clipTimes, medianFirstSound } from '../../lib/speech/clipTimes';

const WORDS = {
  starting: 'Starting', listening: 'Listening', hearing: 'Hearing you', checking: 'Checking', thinking: 'Thinking',
  speaking: 'Speaking',
};
const CUT_IN = 'Talk, or tap the circle, to cut in.';
const HINTS = {
  starting: 'Allow the microphone if your browser asks.', listening: 'Talk whenever you like.',
  hearing: 'Tap the circle to send now.', thinking: CUT_IN, speaking: CUT_IN,
};
const MUTED_HINT = "It can't hear you until you unmute.";
const VERDICTS = { speech: 'Speech', noise: 'Noise', echo: 'Its own voice', failed: 'Failed', refused: 'Refused' };
const COLOURS = {
  listening: 'bg-blue-600', hearing: 'bg-emerald-600', checking: 'bg-amber-600', thinking: 'bg-violet-600',
  speaking: 'bg-sky-600',
};
const LEVEL_GROWTH = 0.45;   // how far the halo grows at full voice, as a share of the circle

const secs = (value) => (value == null ? '–' : `${value.toFixed(2)} s`);

function ClipRow({ clip, silenceMs, theme }) {
  const times = clipTimes(clip, silenceMs);
  return (
    <li data-testid={`clip-${clip.id}`} className={`py-2 border-t ${theme.border}`}>
      <p className="flex flex-wrap gap-x-2">
        <span className="font-bold">{VERDICTS[clip.verdict] ?? 'Checking'}</span>
        <span className="min-w-0 break-words">{clip.text || clip.error}</span>
      </p>
      <p className={theme.textSecondary}>
        {`Speech ${(clip.speechMs / 1000).toFixed(2)} s · no-speech ${clip.scores?.noSpeech?.toFixed(2) ?? '–'}`}
        {` · confidence ${clip.scores?.avgLogprob?.toFixed(2) ?? '–'}`}
      </p>
      <p className={`tabular-nums ${theme.textSecondary}`}>
        {`Whisper ${secs(times.whisper)} · Model ${secs(times.model)} · Voice ${secs(times.voice)}`}
        {` · First sound ${secs(times.firstSound)}`}
      </p>
    </li>
  );
}

/** The circle: its colour is the phase, and its halo grows with the voice (the `--level` the microphone sets). */
function Orb({ phase, muted, orbRef }) {
  const colour = muted ? 'bg-slate-500' : (COLOURS[phase] ?? 'bg-slate-500');
  const busy = phase === 'starting' || phase === 'checking';
  return (
    <span className="relative w-11 h-11 shrink-0 flex items-center justify-center">
      <span ref={orbRef} aria-hidden="true"
        className={`absolute inset-0 rounded-full opacity-40 transition-transform duration-75 ${colour}`}
        style={{ transform: `scale(calc(1 + var(--level, 0) * ${LEVEL_GROWTH}))` }} />
      <span aria-hidden="true" className={`relative w-11 h-11 rounded-full flex items-center justify-center text-white ${
        colour} ${phase === 'speaking' && !muted ? 'animate-pulse' : ''}`}>
        {busy ? <Loader2 size={20} className="animate-spin" /> : <AudioLines size={20} />}
      </span>
    </span>
  );
}

/** Talk, beside the typing box: it starts the conversation at once, or says what it's waiting for. */
export function TalkButton({ theme, status, selectedModel, busy, onStart }) {
  let why = null;
  if (status?.state === 'error') why = "Voice input isn't working right now. Ask your admin";
  else if (!status?.ready) why = 'The speech model is getting ready';
  else if (!selectedModel) why = 'Pick a model first';
  else if (busy) why = 'Wait for the reply to finish';
  return (
    <button type="button" onClick={onStart} disabled={Boolean(why)} aria-label="Talk"
      title={why ?? 'Talk: a spoken conversation, back and forth'}
      className={`p-3 rounded-xl border ${theme.border} ${theme.bgTertiary} ${theme.textSecondary} hover:text-blue-500 transition-colors disabled:opacity-50`}>
      <AudioLines size={18} />
    </button>
  );
}

/**
 * The conversation's controls, in place of the typing box while it runs; the chat above shows its words. It holds no
 * state of its own: the conversation lives in useSpeechWindow.
 */
/**
 * `settings`: what Talk listens with, the server's defaults and the person's choices (lib/speech/talkSettings.js).
 * `onSettings`: opens Settings at the Talk tab.
 */
export default function TalkPanel({ theme, speech, silenceMs, orbRef, heardBy = 'this server', settings = null,
  onSettings = null }) {
  const { phase, clips, muted, microphone = null, end, toggleMute, cutIn, send } = speech;
  const panel = useRef(null);
  const heardList = useRef(null);
  useEffect(() => { panel.current?.focus(); }, []);
  // The list scrolls inside a fixed height; a new clip scrolls it to the bottom, where the newest is.
  useEffect(() => {
    if (heardList.current) heardList.current.scrollTop = heardList.current.scrollHeight;
  }, [clips.length]);
  // A muted microphone sends no more frames: the halo would stay where the last one left it.
  useEffect(() => { if (muted) orbRef?.current?.style.setProperty('--level', '0'); }, [muted, orbRef]);
  const median = medianFirstSound(clips, silenceMs);
  const hint = muted ? MUTED_HINT : HINTS[phase];
  const button = `min-h-11 px-3 rounded-xl text-sm font-bold flex items-center gap-2 border ${theme.border}`;
  const orb = <Orb phase={phase} muted={muted} orbRef={orbRef} />;

  return (
    <div ref={panel} role="group" aria-label="Talk" tabIndex={-1} className="flex flex-col gap-2 outline-none"
      onKeyDown={(e) => {
        if (e.key !== 'Escape') return;
        e.preventDefault();
        e.stopPropagation();
        end();
      }}>
      <div className="flex flex-wrap items-center gap-3">
        {phase === 'speaking' || phase === 'thinking' ? (
          <button type="button" onClick={cutIn} aria-label="Cut in" title="Tap to cut in"
            className="rounded-full focus:outline-none focus:ring-2 focus:ring-blue-500">
            {orb}
          </button>
        ) : phase === 'hearing' && !muted ? (
          <button type="button" onClick={send} aria-label="Send now" title="Tap to send now"
            className="rounded-full focus:outline-none focus:ring-2 focus:ring-blue-500">
            {orb}
          </button>
        ) : orb}
        {/* At least 10rem: on a phone the buttons wrap below it, instead of the words wrapping one per line. */}
        <div className={`flex-1 min-w-40 ${theme.text}`}>
          <p role="status" className="text-sm font-bold">{muted ? 'Muted' : WORDS[phase]}</p>
          {hint && <p className={`text-xs ${theme.textSecondary}`}>{hint}</p>}
          {heardBy && heardBy !== 'this server' && (
            <p className={`text-xs ${theme.textSecondary}`}>{`Heard by ${heardBy}`}</p>
          )}
          {settings?.gate && (
            <p className={`text-xs ${theme.textSecondary}`}>{`Ignoring voices ${settings.gate_db} dB quieter than yours`}</p>
          )}
        </div>
        <div className="flex gap-2">
          {onSettings && (
            <button type="button" onClick={onSettings} aria-label="Talk settings" title="Talk settings"
              className={`${button} ${theme.bgTertiary} ${theme.text}`}>
              <SlidersHorizontal size={16} aria-hidden="true" />
            </button>
          )}
          <button type="button" onClick={toggleMute} className={`${button} ${theme.bgTertiary} ${theme.text}`}>
            {muted ? <Mic size={16} aria-hidden="true" /> : <MicOff size={16} aria-hidden="true" />}
            {muted ? 'Unmute' : 'Mute'}
          </button>
          <button type="button" onClick={end} className={`${button} bg-red-600 text-white`}>
            <PhoneOff size={16} aria-hidden="true" /> End
          </button>
        </div>
      </div>

      {clips.length > 0 && (
        <details className={`w-full text-xs rounded-xl border p-3 ${theme.border} ${theme.text}`}>
          <summary className="cursor-pointer font-bold">
            {`What it heard (${clips.length})`}
            {median != null && ` · typical first sound ${secs(median)}`}
          </summary>
          <button type="button" className="mt-2 underline"
            onClick={() => navigator.clipboard?.writeText(JSON.stringify({
              settings,
              microphone,
              clips: clips.map((clip) => ({ ...clip, times: clipTimes(clip, silenceMs) })),
            }, null, 2))}>
            Copy numbers
          </button>
          {settings?.auto_gain === false && microphone?.autoGainControl === true && (
            <p className={`mt-2 ${theme.textSecondary}`}>
              Automatic gain is still on: this browser kept it, so other voices in the room are harder to ignore.
            </p>
          )}
          <ul ref={heardList} className="mt-2 max-h-48 overflow-y-auto overscroll-contain">
            {clips.map((clip) => <ClipRow key={clip.id} clip={clip} silenceMs={silenceMs} theme={theme} />)}
          </ul>
        </details>
      )}
    </div>
  );
}
