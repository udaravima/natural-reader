/**
 * What a person can change about how Talk listens, on this device (Settings, Talk): a phone and a laptop in the same
 * room need different values. Each control's default is the deployment's, the window settings in /v1/speech/status
 * (server/speech/window.py: the measured values, docs/DEPLOYMENT.md); a microphone switch defaults to the browser's
 * own processing, on, as Talk has always asked for it. A person's choices are stored apart from the defaults, so a
 * default an admin changes still reaches everyone who left it alone, and "Reset to defaults" forgets them all.
 */

// `fallback`: the measured value, for a server too old to send the setting. Ranges are the server's own.
export const TALK_CONTROLS = [
  {
    key: 'auto_gain', group: 'Microphone', kind: 'switch', label: 'Automatic gain', fallback: true,
    help: 'Evens out your voice when you sit closer or further away. It also turns other voices up whenever you stop, '
      + 'so with "Ignore quieter voices" it works best off.',
  },
  {
    key: 'noise_suppression', group: 'Microphone', kind: 'switch', label: 'Noise suppression', fallback: true,
    help: 'Takes out steady noise, a fan or traffic.',
  },
  {
    key: 'echo_cancellation', group: 'Microphone', kind: 'switch', label: 'Echo cancellation', fallback: true,
    help: 'Keeps a reply from hearing itself through the speakers. Turn it off only with headphones.',
  },
  {
    key: 'gate', group: 'Other voices in the room', kind: 'switch', label: 'Ignore quieter voices', fallback: false,
    help: 'For a busy room: a voice much quieter than yours, someone a few desks away, counts as silence, so your '
      + 'turn ends when you stop. It goes by loudness, so a colleague talking nonstop close by can still get through: '
      + 'a headset microphone works best.',
  },
  {
    key: 'gate_db', group: 'Other voices in the room', kind: 'range', label: 'How much quieter', unit: 'dB',
    min: 3, max: 30, step: 1, fallback: 9, onlyWith: 'gate',
    help: 'If it cuts you off, raise it, or sit closer. Lower keeps a nearer voice out more often, and cuts more of yours.',
  },
  {
    key: 'silence_ms', group: 'Ending your turn', kind: 'range', label: 'Pause that ends your turn', unit: 'ms',
    min: 100, max: 5000, step: 100, fallback: 700,
    help: 'Raise it if Talk answers while you are still thinking; lower it for quicker replies.',
  },
  {
    key: 'min_speech_ms', group: 'Ending your turn', kind: 'range', label: 'Speech that counts as a turn', unit: 'ms',
    min: 0, max: 2000, step: 50, fallback: 250,
    help: 'Shorter, and a reply pauses sooner when you speak; longer, and coughs and short noises are ignored more.',
  },
  {
    key: 'vad_start', group: 'Ending your turn', kind: 'range', label: 'How sure it must be that it\'s speech',
    min: 0, max: 1, step: 0.05, fallback: 0.5,
    help: 'Higher ignores more noise and may miss a quiet word.',
  },
];

const usable = (control, value) => (control.kind === 'switch'
  ? typeof value === 'boolean'
  : typeof value === 'number' && Number.isFinite(value) && value >= control.min && value <= control.max);

/** Each control's default: the server's value when it sent a usable one, else the measured one. */
export function talkDefaults(window) {
  return Object.fromEntries(TALK_CONTROLS.map((c) => [c.key, usable(c, window?.[c.key]) ? window[c.key] : c.fallback]));
}

/** What Talk listens with: the server's window settings, with the person's usable choices on top. */
export function talkSettings(window, choices) {
  const mine = choices && typeof choices === 'object' ? choices : {};
  const out = { ...window, ...talkDefaults(window) };
  for (const control of TALK_CONTROLS) {
    if (usable(control, mine[control.key])) out[control.key] = mine[control.key];
  }
  if (typeof out.vad_stay === 'number') out.vad_stay = Math.min(out.vad_stay, out.vad_start);   // the VAD refuses a higher one
  return out;
}

/** A value as a person reads it: "on", "700 ms", "9 dB", "0.50". */
export function shownValue(control, value) {
  if (control.kind === 'switch') return value ? 'on' : 'off';
  if (!control.unit) return value.toFixed(2);
  return `${value} ${control.unit}`;
}
