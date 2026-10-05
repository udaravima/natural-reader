/**
 * The Settings page's context-size and keep-alive choices within the
 * deployment's limits (v2.4 Task F). The server clamps whatever is sent
 * (server/services/inference_limits.py); this only stops the page offering,
 * or showing, a value the server won't honour. A model's limits come with it
 * in the models list (`limits: { numCtxMax, keepAliveMaxS }`, null = none).
 */

export const NUM_CTX_OPTIONS = [['auto', 'Auto'], ['4096', '4096'], ['8192', '8192'], ['16384', '16384'],
    ['32768', '32768'], ['65536', '65536']];
export const KEEP_ALIVE_OPTIONS = [['auto', 'Auto (5m)'], ['5m', '5 minutes'], ['30m', '30 minutes'],
    ['1h', '1 hour'], ['-1', 'Always']];

const UNIT_S = { s: 1, m: 60, h: 3600 };

// An option's size: tokens for a context, seconds for keep-alive ("Always" is
// Infinity); 'auto' is 0, so it is always allowed.
function size(value, kind) {
    if (value === 'auto') return 0;
    if (kind === 'numCtx') return Number(value);
    if (value === '-1' || Number(value) < 0) return Infinity;
    const m = /^(\d+)([smh])?$/.exec(String(value));
    return m ? Number(m[1]) * UNIT_S[m[2] || 's'] : Infinity;
}

/** The options not above `max` (null = every option). */
export function withinLimit(options, max, kind) {
    if (max === null || max === undefined) return options;
    return options.filter(([v]) => size(v, kind) <= max);
}

/** What to show for a saved value: itself if offered, else the largest
 * offered option not above it (the value the server clamps it to). */
export function shownValue(saved, options, kind) {
    if (options.some(([v]) => v === saved)) return saved;
    const target = size(saved, kind);
    const below = options.filter(([v]) => size(v, kind) <= target);
    return (below.at(-1) || options[0])[0];
}
