import { Loader2, Mic, MicOff, PhoneOff } from 'lucide-react';
import { SearchableSelect } from '../SearchableList';
import { useSpeechWindow } from '../../hooks/useSpeechWindow';
import { clipTimes, medianFirstSound } from '../../lib/speech/clipTimes';

const WORDS = {
  off: 'Ready', starting: 'Starting', listening: 'Listening', hearing: 'Hearing you', checking: 'Checking',
  thinking: 'Thinking', speaking: 'Speaking',
};
const VERDICTS = { speech: 'Speech', noise: 'Noise', echo: 'Its own voice', failed: 'Failed', refused: 'Refused' };
const COLOURS = {
  listening: 'bg-blue-600', hearing: 'bg-emerald-600', checking: 'bg-amber-600', thinking: 'bg-violet-600',
  speaking: 'bg-sky-600',
};
const RECENT_MESSAGES = 6;

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

function Notice({ status, canTalkToModel, theme }) {
  let words = null;
  if (status && !status.ready) words = 'The speech model is getting ready.';
  else if (!canTalkToModel) words = 'Pick a model first.';
  return words && <p className={`text-xs ${theme.textSecondary}`}>{words}</p>;
}

/** The speech window's screen. It holds no state of its own: the conversation lives in useSpeechWindow. */
export default function SpeechWindow({
  theme, status, speech, messages, models, selectedModel, setSelectedModel,
}) {
  if (status && !status.enabled) {
    return (
      <div className={`flex-1 flex items-center justify-center p-6 ${theme.textSecondary}`}>
        <p className="text-sm">Voice input is off, so there&apos;s nothing to talk to. An admin can turn it on.</p>
      </div>
    );
  }
  const { phase, clips, muted, start, end, toggleMute } = speech;
  const running = phase !== 'off';
  const silenceMs = status?.window?.silence_ms ?? 0;
  const median = medianFirstSound(clips, silenceMs);
  const word = muted ? 'Muted' : WORDS[phase];
  const recent = messages.filter((m) => m.content).slice(-RECENT_MESSAGES);
  const canStart = Boolean(status?.ready && selectedModel);
  const button = `min-h-11 px-4 rounded-xl text-sm font-bold flex items-center gap-2 border ${theme.border}`;

  return (
    <div className={`flex-1 min-h-0 overflow-y-auto p-4 sm:p-6 ${theme.text}`}>
      <div className="max-w-xl mx-auto flex flex-col items-center gap-4">
        <div role="status" className={`w-36 h-36 rounded-full flex flex-col items-center justify-center gap-1 text-white ${
          muted ? 'bg-slate-500' : (COLOURS[phase] ?? 'bg-slate-500')}`}>
          {phase === 'starting' ? <Loader2 size={28} className="animate-spin" aria-hidden="true" /> : <Mic size={28} aria-hidden="true" />}
          <span className="text-sm font-bold">{word}</span>
        </div>

        <div className="flex flex-wrap justify-center gap-2">
          {!running && (
            <button type="button" onClick={start} disabled={!canStart}
              className={`${button} bg-blue-600 text-white disabled:opacity-50`}>
              <Mic size={16} aria-hidden="true" /> Start
            </button>
          )}
          {running && (
            <>
              <button type="button" onClick={toggleMute} className={`${button} ${theme.bgTertiary}`}>
                <MicOff size={16} aria-hidden="true" /> {muted ? 'Unmute' : 'Mute'}
              </button>
              <button type="button" onClick={end} className={`${button} bg-red-600 text-white`}>
                <PhoneOff size={16} aria-hidden="true" /> End
              </button>
            </>
          )}
        </div>
        <Notice status={status} canTalkToModel={Boolean(selectedModel)} theme={theme} />

        {models.length > 0 && (
          <div className="w-full max-w-xs">
            <SearchableSelect theme={theme} aria-label="Chat model" findLabel="Find a model" value={selectedModel}
              onChange={setSelectedModel} options={[
                ...(selectedModel ? [] : [{ value: '', label: 'Choose a model' }]),
                ...models.map((m) => ({ value: m.id, label: m.name || m.id })),
              ]}
              className={`w-full min-h-11 px-3 text-sm rounded-xl border ${theme.border} ${theme.bg} ${theme.text}`} />
          </div>
        )}

        {recent.length > 0 && (
          <ul className="w-full space-y-2 text-sm">
            {recent.map((m) => (
              <li key={m.id} className={`p-3 rounded-xl break-words ${m.role === 'user' ? `${theme.bgTertiary} ml-8` : 'mr-8'}`}>
                {m.content}
              </li>
            ))}
          </ul>
        )}

        {clips.length > 0 && (
          <details className={`w-full text-xs rounded-xl border p-3 ${theme.border}`}>
            <summary className="cursor-pointer font-bold">
              {`What it heard (${clips.length})`}
              {median != null && ` · typical first sound ${secs(median)}`}
            </summary>
            <button type="button" className="mt-2 underline"
              onClick={() => navigator.clipboard?.writeText(JSON.stringify(clips.map((clip) => ({
                ...clip, times: clipTimes(clip, silenceMs),
              })), null, 2))}>
              Copy numbers
            </button>
            <ul className="mt-2">
              {clips.map((clip) => <ClipRow key={clip.id} clip={clip} silenceMs={silenceMs} theme={theme} />)}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

/** The window with its conversation. Mounted only in Talk, so leaving the view releases the microphone. */
export function SpeechView({
  theme, status, messages, models, selectedModel, setSelectedModel, ...conversation
}) {
  const speech = useSpeechWindow({ ...conversation, settings: status?.window });
  return (
    <SpeechWindow theme={theme} status={status} speech={speech} messages={messages} models={models}
      selectedModel={selectedModel} setSelectedModel={setSelectedModel} />
  );
}
