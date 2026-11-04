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

/** [{code, label, detail}] for the switch. `current`: the Speech language; `talkLanguage`: what Auto means in Talk. */
export function speakingOptions(status, current, talkLanguage) {
  const enabled = status?.enabled === true;
  const names = new Map((enabled && Array.isArray(status.languages) ? status.languages : []).map((l) => [l.code, l.name]));
  const remote = enabled && status.engine !== 'local';
  // Auto's: the default model, or the first Google recognizer; else who hears (a remote server).
  const byDefault = enabled ? (status.default_model?.label ?? (remote ? (status.heard_by ?? '') : '')) : '';
  const out = [{ code: 'auto', label: 'Auto', detail: byDefault }];
  if (!enabled) return out;
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
