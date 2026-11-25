/** Words for a phone call's facts, shared by the phone lines editor and the Calls section. */

export const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// Why a call ended (phone_calls.end_reason); null means it hasn't.
export const END_REASONS = {
  caller: 'caller hung up', limit: 'reached the time limit', idle: 'no one spoke', budget: "line's token budget was used up",
  busy: 'line was busy', closed: 'line was closed', error: 'something went wrong', shutdown: 'app was restarted',
};

export function lengthText(seconds) {
  if (seconds == null) return 'in progress';
  const s = Math.round(seconds);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} m ${s % 60} s`;
}
