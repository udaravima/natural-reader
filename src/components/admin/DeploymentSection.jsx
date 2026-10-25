import { useEffect, useMemo } from 'react';
import { useSectionSettings } from '../../hooks/useSectionSettings';
import { zoneOptions } from '../../lib/zoneFormat';
import { SettingRow, SectionSaveBar, SourceLine } from './SettingRow';

/**
 * Admin console: Deployment (spec §4.5). The one setting the console edits here is the
 * timezone (it decides where a day, and so the token budget's reset, begins). Below it,
 * what the server runs with, read-only: those values belong to the deployer's
 * environment, so each says which variable to change. A secret is only ever "set" or
 * "not set"; the server never sends its value.
 */

const TIMEZONE_HELP = "Decides when each day's token budget resets and which day usage counts toward. Changing it moves today's reset once, so someone may get a fresh budget a few hours early or late.";
const ID = 'deployment-timezone';
const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';

export function DeploymentSection({ theme, apiHost, apiPort, showToast, onDirtyChange, onSaved }) {
  const s = useSectionSettings(apiHost, apiPort, 'deployment');
  const zone = s.value('timezone');
  // Some 400 <option>s: built once per list, so a keystroke elsewhere doesn't re-render them all.
  const options = useMemo(() => zoneOptions(zone).map((z) => <option key={z} value={z}>{z}</option>), [zone]);

  useEffect(() => { onDirtyChange?.(s.dirty); }, [s.dirty, onDirtyChange]);

  const save = async () => {
    if (!(await s.save())) return;
    // The save answered with the zone now in force, which is what the rest of the console should show.
    showToast?.('Timezone saved. It applies from the next request.', 4000);
    onSaved?.(zone);
  };

  return (
    <section className="flex flex-col gap-4" aria-label="Deployment">
      <h2 className={sectionTitle}>Deployment</h2>
      {s.status === 'loading' && <p className={`text-xs ${theme.textMuted}`}>Loading…</p>}
      {s.status === 'failed' && <p className="text-xs text-red-500">Couldn&apos;t load the deployment settings.</p>}
      {s.status === 'ready' && (
        <>
          <div className={`flex flex-col gap-3 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
            <SettingRow
              theme={theme} id={ID} label="Timezone" help={TIMEZONE_HELP}
              source={s.source('timezone')} envName={s.envName('timezone')}
              canUseServer={s.canUseServer('timezone')} onUseServer={() => s.useServer('timezone')}
            >
              <select
                id={ID} value={zone} disabled={s.saving}
                onChange={(e) => s.set('timezone', e.target.value)}
                aria-describedby={`${ID}-help ${ID}-source`}
                className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-h-11 md:min-h-0`}
              >
                {options}
              </select>
            </SettingRow>
            <SectionSaveBar theme={theme} dirty={s.dirty} saving={s.saving} error={s.error} onSave={save} onDiscard={s.discard} />
          </div>

          <div className="flex flex-col gap-2">
            <h3 className={`text-xs font-bold ${theme.text}`}>Server settings (read-only)</h3>
            <ul className={`flex flex-col gap-2 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
              {s.info.map((row) => (
                <li key={row.id} data-testid={`info-${row.id}`} className="flex flex-col gap-0.5 text-xs">
                  <div className="flex flex-wrap justify-between gap-x-4">
                    <span className={theme.textMuted}>{row.label}</span>
                    <span className="break-all text-right">{row.value}</span>
                  </div>
                  <span className={`text-[10px] ${theme.textSecondary}`}><SourceLine source={row.source} envName={row.env} /></span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </section>
  );
}
