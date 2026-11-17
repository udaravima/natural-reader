const seconds = (from, to) => (from != null && to != null ? (to - from) / 1000 : null);
const round = (value) => (value == null ? null : Math.round(value * 1000) / 1000);
const minus = (whole, part) => (whole != null && part != null ? round(Math.max(0, whole - part)) : null);

/**
 * How long each step of a clip took, in seconds. The VAD reports the end of speech only after its silence, so the
 * wait a person feels is that silence plus everything after it.
 *
 * `whisper`, `model` and `voice` are the three stages before the first sound, timed in the browser. Beside each is
 * the server's own time on it where the server says (`heardEngine`, `voiceEngine`): for an engine in the cloud that
 * is the cloud's latency, and what is left over (`heardRest`, `voiceRest`) is the upload, the network and any wait
 * in a queue. `firstWord`, `reply` and `wordsPerSecond` describe the model's whole answer. `slowest` names the
 * stage that cost most: 'heard', 'model' or 'voice'.
 */
export function clipTimes(clip, silenceMs) {
  const whisper = seconds(clip.endedAt, clip.transcribedAt);
  const model = seconds(clip.transcribedAt, clip.firstSentenceAt);
  const voice = seconds(clip.firstSentenceAt, clip.firstAudioAt);
  const spoken = (clip.sentences ?? []).filter((s) => s.doneAt != null);
  const first = spoken[0];
  const writing = seconds(clip.firstTextAt, clip.replyDoneAt);
  const stages = [['heard', whisper], ['model', model], ['voice', voice]].filter(([, value]) => value != null);
  return {
    whisper,
    model,
    voice,
    firstSound: clip.firstAudioAt != null ? (clip.firstAudioAt - clip.endedAt + silenceMs) / 1000 : null,
    heardEngine: clip.heardEngineSeconds ?? null,
    heardRest: minus(whisper, clip.heardEngineSeconds),
    heardWhere: clip.heardWhere ?? null,
    firstWord: seconds(clip.transcribedAt, clip.firstTextAt),
    reply: seconds(clip.transcribedAt, clip.replyDoneAt),
    wordsPerSecond: writing > 0 && clip.replyWords ? round(clip.replyWords / writing) : null,
    voiceEngine: first?.engineSeconds ?? null,
    voiceRest: first ? minus(seconds(first.startedAt, first.doneAt), first.engineSeconds) : null,
    voiceBy: first?.engine ?? null,
    voiceWhere: first?.where ?? null,
    sentences: spoken.length,
    slowestSentence: spoken.length ? Math.max(...spoken.map((s) => seconds(s.startedAt, s.doneAt))) : null,
    slowest: stages.length === 3 ? stages.reduce((a, b) => (b[1] > a[1] ? b : a))[0] : null,
  };
}

export function medianFirstSound(clips, silenceMs) {
  const times = clips.map((clip) => clipTimes(clip, silenceMs).firstSound).filter((t) => t != null).sort((a, b) => a - b);
  if (times.length === 0) return null;
  const middle = Math.floor(times.length / 2);
  return times.length % 2 ? times[middle] : (times[middle - 1] + times[middle]) / 2;
}
