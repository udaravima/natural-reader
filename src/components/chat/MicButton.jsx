import { useEffect, useRef } from 'react';
import { Mic, Square, X, Loader2 } from 'lucide-react';
import { HARD_REASONS } from '../../hooks/useVoiceInput';

const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

/**
 * The composer's microphone (spec §4.1), driven by useVoiceInput's result `voice`. Not rendered at all when
 * voice input is off. Idle: a mic, disabled with the reason as its title when the browser can't record at all
 * (no https, no MediaRecorder); a blocked microphone only shows its reason, because the person may allow it
 * in the site settings and press again. Recording: Stop (with the timer) and Cancel. Transcribing: the
 * frozen timer, "Transcribing…" and Cancel.
 */
export default function MicButton({ theme, voice, disabled = false }) {
  const stopRef = useRef(null);
  // Start swaps the mic for Stop, which would drop the focus to the page: put it on Stop, so Enter stops.
  useEffect(() => {
    if (voice.phase === 'recording') stopRef.current?.focus();
  }, [voice.phase]);
  if (!voice.available) return null;
  const base = `min-h-11 min-w-11 p-3 rounded-xl border ${theme.border} flex items-center justify-center gap-1.5 transition-colors`;

  if (voice.phase === 'idle') {
    const hard = HARD_REASONS.includes(voice.reason);
    return (
      <button
        type="button"
        onClick={voice.start}
        disabled={hard || disabled}
        aria-label="Speak a message"
        title={voice.reason || 'Speak a message'}
        className={`${base} ${theme.bgTertiary} ${theme.textSecondary} hover:text-blue-500 disabled:opacity-50`}
      >
        <Mic size={18} />
      </button>
    );
  }

  const timer = <span aria-live="off" className="text-xs tabular-nums">{clock(voice.seconds)}</span>;
  const cancel = (
    <button
      type="button"
      onClick={voice.cancel}
      aria-label="Cancel"
      title="Cancel (Esc)"
      className={`${base} ${theme.bgTertiary} ${theme.textSecondary} hover:text-red-500`}
    >
      <X size={18} />
    </button>
  );

  if (voice.phase === 'recording') {
    return (
      <div className="flex items-center gap-1">
        <button
          type="button"
          ref={stopRef}
          onClick={voice.stop}
          aria-label="Stop and transcribe"
          title="Stop and transcribe"
          className={`${base} bg-red-500 hover:bg-red-600 text-white border-transparent`}
        >
          <Square size={16} fill="currentColor" />
          {timer}
        </button>
        {cancel}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1">
      <span role="status" className={`min-h-11 px-3 flex items-center gap-1.5 text-xs ${theme.textSecondary}`}>
        <Loader2 size={16} className="animate-spin" aria-hidden="true" />
        Transcribing…
      </span>
      {timer}
      {cancel}
    </div>
  );
}
