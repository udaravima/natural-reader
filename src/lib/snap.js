/**
 * A slider that moves linearly but is pulled onto round values, like the detents of a knob (the owner, 2026-11-04:
 * "linear control but kind of like magnetic to those preferred hop numbers ... with some extra force").
 *
 * Around every multiple of `every` there is a pull: a dragged value inside it lands on the round value, and the thumb
 * stays there until the pointer has moved past the pull's edge, the "extra force". Between pulls the value follows
 * the pointer to the `fine` step. `home` values (1x for speed) pull harder. The pull is set in pixels and turned into
 * value units for the track's width (pullFor), so it feels the same on a desktop and a phone. It is capped at 40% of the
 * gap between round values (a home value's at half), so part of every gap is always free: the values in between can
 * always be reached by dragging a little further (pulls).
 *
 * Keys and screen readers don't drag: they step to the next round value (stepTo), and Shift with a key moves by the
 * fine step (fineStep).
 */

const TYPICAL_TRACK_PX = 300;   // for a track not laid out yet (width 0)

const decimalsOf = (n) => {
  const text = String(n);
  return text.includes('.') ? text.split('.')[1].length : 0;
};

/** `x` without floating-point dust, to the finer of the two grids. */
const tidy = (x, fine, every) => Number(x.toFixed(Math.max(decimalsOf(fine), decimalsOf(every ?? fine))));

const clamp = (x, min, max) => Math.min(max, Math.max(min, x));

/**
 * Where a dragged value lands. `pull` and `homePull` are in value units (pullFor).
 * @param {number} raw the value under the pointer
 * @param {{min:number,max:number,fine:number,every:number,home?:number[],pull:number,homePull?:number}} o
 */
export function magnet(raw, { min, max, fine, every, home = [], pull, homePull = pull }) {
  const x = clamp(raw, min, max);
  for (const h of home) {
    if (Math.abs(x - h) <= homePull) return h;
  }
  const round = clamp(Math.round(x / every) * every, min, max);
  if (Math.abs(x - round) <= pull) return tidy(round, fine, every);
  return tidy(clamp(Math.round(x / fine) * fine, min, max), fine, every);
}

/** The next round value from `value` in `direction` (1 up, -1 down), within the range. */
export function stepTo(value, direction, { min, max, fine, every }) {
  const index = value / every;
  const next = direction > 0 ? (Math.floor(index + 1e-9) + 1) * every : (Math.ceil(index - 1e-9) - 1) * every;
  return tidy(clamp(next, min, max), fine, every);
}

/** One fine step from `value` in `direction`, within the range. */
export function fineStep(value, direction, { min, max, fine, every }) {
  return tidy(clamp(value + direction * fine, min, max), fine, every);
}

/** `px` on a track `trackPx` wide, as a span of value. */
export function pullFor(px, trackPx, min, max) {
  return (px / (trackPx > 0 ? trackPx : TYPICAL_TRACK_PX)) * (max - min);
}

const PULL_SHARE = 0.4;    // of the gap between two round values, at most: about 80% of a drag lands on one
const HOME_SHARE = 0.5;

/** Both pulls, in value units, for a track `trackPx` wide: the pixels asked for, capped so each gap keeps a free part. */
export function pulls(trackPx, { min, max, every }, pullPx, homePullPx) {
  const gap = every;
  return {
    pull: Math.min(pullFor(pullPx, trackPx, min, max), PULL_SHARE * gap),
    homePull: Math.min(pullFor(homePullPx, trackPx, min, max), HOME_SHARE * gap),
  };
}
