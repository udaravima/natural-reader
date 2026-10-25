/**
 * Days in the deployment's timezone (admin console v2 spec §4.5). The server counts a
 * day, and resets the token budget, at local midnight in one configured zone, so the
 * console shows dates in that zone too instead of slicing a UTC string.
 */

/** 'UTC' first, then every zone the browser knows, plus `current` if the list lacks it (an older or newer zone database). */
export function zoneOptions(current) {
  let known = [];
  try {
    known = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  } catch { /* an engine that lists no zones: just UTC */ }
  const zones = ['UTC', ...known.filter((z) => z !== 'UTC')];
  if (current && !zones.includes(current)) zones.push(current);
  return zones;
}

/** A timestamp as YYYY-MM-DD in `zone`. A zone Intl refuses reads as UTC; a value that isn't a date shows its first 10 characters. */
export function formatDay(value, zone) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  const format = (timeZone) => new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
  try {
    return format(zone || 'UTC');
  } catch {
    return format('UTC');
  }
}
