import { TALK_CONTROLS, talkDefaults, talkSettings, shownValue } from '../../lib/speech/talkSettings';
import { SnapSlider } from '../SnapSlider';

const GROUPS = [...new Set(TALK_CONTROLS.map((c) => c.group))];

/**
 * Settings, Talk: how Talk listens on this device. Each control shows its default, this server's known-good value, and
 * one button forgets every change. A control moved back to its default forgets that choice, so it follows the server
 * again if an admin changes the default. Changes apply the next time Talk starts.
 *
 * `t`: { defaults: the server's window settings (null until /v1/speech/status answers), choices, setChoices }.
 */
export function TalkSettingsPanel({ theme, t }) {
  const defaults = talkDefaults(t.defaults);
  const current = talkSettings(t.defaults, t.choices);
  const chosen = t.choices && typeof t.choices === 'object' ? t.choices : {};
  const set = (key, value) => t.setChoices((prev) => {
    const next = { ...(prev && typeof prev === 'object' ? prev : {}) };
    if (value === defaults[key]) delete next[key]; else next[key] = value;
    return next;
  });
  const muted = `text-[10px] ${theme.textMuted}`;
  return (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className={`text-xs ${theme.textSecondary}`}>
          How Talk listens on this device, from the next time you start it. Each default is this server&apos;s
          tested value.
        </p>
        <button type="button" onClick={() => t.setChoices({})} disabled={Object.keys(chosen).length === 0}
          className={`shrink-0 text-xs font-bold px-3 py-2 rounded-lg border ${theme.border} ${theme.bgTertiary} ${theme.text} ${theme.hover} disabled:opacity-40`}>
          Reset to defaults
        </button>
      </div>
      {GROUPS.map((group) => (
        <section key={group} className="flex flex-col gap-3">
          <h3 className={`text-[10px] font-bold uppercase tracking-wider ${theme.textMuted}`}>{group}</h3>
          {TALK_CONTROLS.filter((c) => c.group === group && (!c.onlyWith || current[c.onlyWith])).map((c) => (
            <div key={c.key} role="group" aria-label={c.label}
              className={`flex flex-col gap-1 p-2 rounded-lg border ${theme.border} ${theme.bgTertiary}`}>
              <div className="flex items-center justify-between gap-3">
                <span className={`text-xs font-bold ${theme.text}`}>{c.label}</span>
                {c.kind === 'switch' ? (
                  // The button is the touch target (index.css gives every phone button 44 px); the track inside is the switch.
                  <button type="button" role="switch" aria-checked={current[c.key]} aria-label={c.label}
                    onClick={() => set(c.key, !current[c.key])}
                    className="shrink-0 flex items-center justify-end min-h-11 min-w-11 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded-lg">
                    <span className={`relative block w-10 h-6 rounded-full transition-colors ${current[c.key] ? 'bg-blue-600' : 'bg-gray-400'}`}>
                      <span className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-all ${current[c.key] ? 'left-5' : 'left-1'}`} />
                    </span>
                  </button>
                ) : (
                  <span className={`text-xs font-bold tabular-nums ${theme.text}`}>{shownValue(c, current[c.key])}</span>
                )}
              </div>
              {c.kind === 'range' && (
                <SnapSlider label={c.label} value={current[c.key]} min={c.min} max={c.max} fine={c.fine} every={c.step}
                  valueText={(value) => shownValue(c, value)} onChange={(value) => set(c.key, value)}
                  className="w-full accent-blue-600" />
              )}
              <p className={muted}>
                {`Default: ${shownValue(c, defaults[c.key])}`}
              </p>
              <p className={muted}>{c.help}</p>
            </div>
          ))}
        </section>
      ))}
    </>
  );
}
