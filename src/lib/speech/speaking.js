/**
 * What the Speaking switch lists (spec 2026-11-04-speech-choices-per-person): the languages a person can switch to
 * in one tap, beside the microphone and in Talk, each with what hears it. The value is the person's Speech language
 * ('auto' or a code), the same one Settings → Chat holds; the server picks the model from it.
 *
 * - Whisper here (`engine: 'local'`): Auto, the Talk language (`window.language`, heard by the default model), each
 *   language an admin offered a model for (`hears`), in the admin's order.
 * - Google: Auto and the recognizers' languages (`languages`, already only theirs), each with the recognizer that
 *   hears it when an admin ticked several (`hears`, spec 2026-11-04-google-recognizers).
 * - An OpenAI-compatible server: Auto and the Talk language.
 * And always the person's current language, if it isn't listed: one picked from Settings' full list still shows.
 */

/**
 * The language Auto is heard in, when it can only be one: Google sends Auto to the first recognizer, and one that
 * lists a single language (or, like `long`, hears only its first: `auto_hears`) hears that. Whisper detects, and so
 * does a `chirp_3` recognizer with several languages: then none.
 */
function autoMeans(status, names) {
  if (status.engine !== 'google') return null;
  if (typeof status.auto_hears === 'string' && status.auto_hears) return status.auto_hears;
  const first = status.default_model?.id;
  const firsts = Array.isArray(status.hears) && first
    ? status.hears.filter((h) => h.model === first).map((h) => h.code)
    : [...names.keys()];
  return firsts.length === 1 ? (names.get(firsts[0]) ?? null) : null;
}

/** [{code, label, detail, means?}] for the switch; Auto's `means` is the one language it can be heard in, if so. `current`: the Speech language; `talkLanguage`: what Auto means in Talk. */
export function speakingOptions(status, current, talkLanguage) {
  const enabled = status?.enabled === true;
  const names = new Map((enabled && Array.isArray(status.languages) ? status.languages : []).map((l) => [l.code, l.name]));
  const remote = enabled && status.engine !== 'local';
  // Auto's: the default model, or the first Google recognizer; else who hears (a remote server).
  const byDefault = enabled ? (status.default_model?.label ?? (remote ? (status.heard_by ?? '') : '')) : '';
  const out = [{ code: 'auto', label: 'Auto', detail: byDefault }];
  if (!enabled) return out;
  const means = autoMeans(status, names);
  if (means) out[0].means = means;
  const seen = new Set(['auto']);
  const add = (code, detail) => {
    if (!code || seen.has(code)) return;
    seen.add(code);
    out.push({ code, label: names.get(code) ?? code, detail });
  };
  const offered = new Map((Array.isArray(status.hears) ? status.hears : []).map((h) => [h.code, h.label]));
  if (status.engine === 'google') {
    for (const code of names.keys()) add(code, offered.get(code) ?? status.heard_by ?? '');
  } else {
    add(talkLanguage, offered.get(talkLanguage) ?? byDefault);
    for (const [code, label] of offered) add(code, label);
  }
  if (current && current !== 'auto' && (status.engine !== 'google' || names.has(current))) add(current, offered.get(current) ?? byDefault);
  return out;
}
