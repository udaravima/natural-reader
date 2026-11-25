import { formatClock, formatMoment } from '../../lib/zoneFormat';
import { END_REASONS, lengthText, plural } from '../../lib/phoneCalls';

/**
 * What the two places that show phone calls share: the per-line list in a line's editor and the console's Calls
 * section. A call's facts in words, its transcript with the timings of each turn, and the Refresh control.
 */

// A turn's error (server/phone/call.py: the stats entry's `error`), in words; any other code is a failed answer.
// `hearing` (a clip the recognizer failed on) is no turn's error: that entry reads "Not heard".
const TURN_ERRORS = { voice: 'a sentence was skipped', empty: 'said nothing' };
const errorWords = (error) => TURN_ERRORS[error] ?? 'the answer failed';
// What an empty reply reads, by what happened to that message: still being written, failed before any text, or cut
// off (the caller cut in or hung up first).
const emptyReply = (status) => (status === 'streaming' ? '…' : status === 'error' ? '(no answer)' : '(cut off before answering)');
const replyText = (m) => m.content || (m.role === 'assistant' ? emptyReply(m.status) : '');

/** Seconds since the call began, as m:ss. */
const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

/** A stats entry's seconds from the caller stopping to `at`, to one place; null when either is missing. The entry's
 * times are seconds since the call started; first sound is first_audio_at - stopped_at (Talk's firstSound), heard
 * after is heard_at - stopped_at (the recognizer's answer). */
function since(s, at) {
  return typeof at === 'number' && typeof s.stopped_at === 'number' ? (at - s.stopped_at).toFixed(1) : null;
}

/** "first sound 2.8 s · heard after 1.3 s", then the error in words: what a reply's timing line says. */
function replyTiming(s) {
  const firstSound = since(s, s.first_audio_at);
  const heard = since(s, s.heard_at);
  return [
    firstSound !== null && `first sound ${firstSound} s`,
    heard !== null && `heard after ${heard} s`,
    s.error && errorWords(s.error),
  ].filter(Boolean).join(' · ');
}

/**
 * Calls recorded before message ids: one line per stats entry, "Turn n: first sound X.X s · heard after Y.Y s", then
 * its error in words. A clip the recognizer failed on is no turn, so it has no number: it reads "Not heard", with the
 * first sound of the "sorry" it got, if it got one. Turns are numbered 1, 2, 3, so a call with 3 turns never shows
 * "Turn 4". (A turn whose claim failed, error `begin`, is numbered: the caller asked, and heard "sorry". The call
 * list's turn count leaves it out.) Other keys (`babble`) are ignored.
 */
function turnLines(stats) {
  let n = 0;
  return stats.map((s) => {
    if (s.error === 'hearing') {
      const firstSound = since(s, s.first_audio_at);
      return firstSound === null ? 'Not heard' : `Not heard: first sound ${firstSound} s`;
    }
    n += 1;
    return `Turn ${n}: ${replyTiming(s)}`;
  });
}

const hasIds = (stats) => stats.some((s) => 'user_message_id' in s);

/**
 * The transcript's rows, in order, each `{ key, kind, text, note }`. A turn's timings sit beside the messages it made,
 * found by their ids (R21.2); counting turns against messages would drift on a misheard clip, a failed claim or an
 * interruption. The caller's line notes when the caller stopped (m:ss since the call began), the line's reply its first
 * sound and when it was heard. An entry with no messages (a clip the recognizer failed on, or a claim that failed)
 * is a row of its own at its time, placed before the first message of a later turn.
 */
function rowsOf(messages, stats) {
  const byUser = new Map();
  const byReply = new Map();
  const alone = [];
  for (const s of stats) {
    if (s.user_message_id) byUser.set(s.user_message_id, s);
    if (s.assistant_message_id) byReply.set(s.assistant_message_id, s);
    if (!s.user_message_id && !s.assistant_message_id) alone.push(s);
  }
  alone.sort((a, b) => (a.stopped_at ?? 0) - (b.stopped_at ?? 0));
  const aloneRow = (s, i) => ({
    key: `alone-${i}`, kind: 'alone',
    // no messages and no error: the caller cut in before the claim landed, nothing failed
    text: s.error === 'hearing' ? `Not heard at ${clock(s.stopped_at ?? 0)}`
      : `At ${clock(s.stopped_at ?? 0)}: ${s.error ? errorWords(s.error) : 'cut in before the answer began'}`,
  });
  const rows = [];
  let next = 0;
  const flushBefore = (time) => {
    while (next < alone.length && (alone[next].stopped_at ?? 0) < time) { rows.push(aloneRow(alone[next], next)); next += 1; }
  };
  for (const m of messages) {
    const turn = m.role === 'user' ? byUser.get(m.id) : byReply.get(m.id);
    if (turn) flushBefore(turn.stopped_at ?? 0);
    const note = !turn ? null : (m.role === 'user'
      ? (typeof turn.stopped_at === 'number' ? `at ${clock(turn.stopped_at)}` : null)
      : (replyTiming(turn) || null));
    rows.push({ key: m.id, kind: m.role, text: replyText(m), note });
  }
  flushBefore(Infinity);
  return rows;
}

/** A call's transcript: the messages, with each turn's timing beside them, or, for a call recorded before message ids,
 * the messages and then the separate list of timings. A call in progress has no timings yet. */
export function CallTranscript({ theme, messages, stats }) {
  const list = Array.isArray(stats) ? stats : [];
  if (!hasIds(list)) {
    return (
      <div data-testid="phone-transcript" className="flex flex-col gap-1 pl-2">
        {messages.map((m) => (
          <p key={m.id} data-testid="phone-row">
            <span className={`font-bold ${theme.textSecondary}`}>{m.role === 'user' ? 'Caller' : 'Line'}: </span>
            <span>{replyText(m)}</span>
          </p>
        ))}
        {list.length > 0 && (
          <ul data-testid="phone-turn-timings" aria-label="Timings per turn" className={`flex flex-col text-[10px] ${theme.textMuted}`}>
            {turnLines(list).map((text, i) => <li key={i}>{text}</li>)}
          </ul>
        )}
      </div>
    );
  }
  return (
    <div data-testid="phone-transcript" className="flex flex-col gap-1 pl-2">
      {rowsOf(messages, list).map((r) => (
        r.kind === 'alone' ? (
          <p key={r.key} data-testid="phone-row" className={`text-[10px] ${theme.textMuted}`}>{r.text}</p>
        ) : (
          <p key={r.key} data-testid="phone-row">
            <span className={`font-bold ${theme.textSecondary}`}>{r.kind === 'user' ? 'Caller' : 'Line'}: </span>
            <span>{r.text}</span>
            {r.note && <span className={`ml-2 text-[10px] ${theme.textMuted}`}>{r.note}</span>}
          </p>
        )
      ))}
    </div>
  );
}

/** Refresh, which says it is working ("Refreshing…", disabled) and, when it is done, when the list was last read. */
export function RefreshControl({ onRefresh, busy, updatedAt, zone, theme }) {
  return (
    <>
      <button type="button" onClick={onRefresh} disabled={busy} className="min-h-11 md:min-h-0 underline text-[10px]">
        {busy ? 'Refreshing…' : 'Refresh'}
      </button>
      {updatedAt && <span className={`text-[10px] ${theme?.textMuted ?? ''}`}>{`Updated ${formatClock(updatedAt, zone)}`}</span>}
    </>
  );
}

/** One call's facts on a row: when it began, how long, why it ended, turns and the slowest first sound. */
export function CallFacts({ call: c, zone }) {
  return (
    <>
      <span>{formatMoment(c.startedAt, zone)}</span>
      {c.endedAt && <span>{lengthText(c.lengthS)}</span>}
      <span>{c.endedAt ? (END_REASONS[c.endReason] ?? c.endReason) : (c.inProgress === false ? 'never finished' : 'in progress')}</span>
      {/* the turn count and the timings are written at hang-up: while the call goes on they would read 0 */}
      {(c.endedAt || c.inProgress === false) && <span>{plural(c.turns, 'turn')}</span>}
      {c.slowestFirstSoundS != null && (c.endedAt || c.inProgress === false) && <span>{`slowest ${c.slowestFirstSoundS} s`}</span>}
    </>
  );
}
