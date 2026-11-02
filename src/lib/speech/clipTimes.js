const seconds = (from, to) => (from != null && to != null ? (to - from) / 1000 : null);

/**
 * How long each step of a clip took, in seconds. The VAD reports the end of speech only after its silence, so the
 * wait a person feels is that silence plus everything after it.
 */
export function clipTimes(clip, silenceMs) {
  return {
    whisper: seconds(clip.endedAt, clip.transcribedAt),
    model: seconds(clip.transcribedAt, clip.firstSentenceAt),
    voice: seconds(clip.firstSentenceAt, clip.firstAudioAt),
    firstSound: clip.firstAudioAt != null ? (clip.firstAudioAt - clip.endedAt + silenceMs) / 1000 : null,
  };
}

export function medianFirstSound(clips, silenceMs) {
  const times = clips.map((clip) => clipTimes(clip, silenceMs).firstSound).filter((t) => t != null).sort((a, b) => a - b);
  if (times.length === 0) return null;
  const middle = Math.floor(times.length / 2);
  return times.length % 2 ? times[middle] : (times[middle - 1] + times[middle]) / 2;
}
