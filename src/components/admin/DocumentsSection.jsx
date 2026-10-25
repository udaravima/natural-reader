import { useEffect, useState } from 'react';
import { useSectionSettings } from '../../hooks/useSectionSettings';
import { SettingRow, SectionSaveBar } from './SettingRow';

/**
 * Admin console: Documents (spec §4.3), the limits on what a Docling conversion may use:
 * who may start one, which presets, OCR, picture descriptions, and how many pages.
 * The console can only narrow what the server's environment allows, so an option the
 * environment turns off is shown disabled, with the variable that did it. Accurate runs
 * OCR, so the server refuses it while OCR is off; the console says so and leaves the
 * stored presets as the admin set them.
 */

const PRESETS = [
  { id: 'fast', label: 'Fast' },
  { id: 'standard', label: 'Standard' },
  { id: 'accurate', label: 'Accurate' },
];
const MAX_PAGES_LIMIT = 100000;
const PAGES_ERROR = `Must be a whole number from 0 to ${MAX_PAGES_LIMIT}.`;
const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';

const validPages = (text) => /^\d+$/.test(text) && Number(text) <= MAX_PAGES_LIMIT;

export function DocumentsSection({ theme, apiHost, apiPort, showToast, onDirtyChange }) {
  const s = useSectionSettings(apiHost, apiPort, 'documents');
  // What is typed in the page limit, while it is typed: a half-typed or invalid number can't live in the
  // settings drafts (they hold values the server accepts). null = show the setting's own value.
  const [pagesText, setPagesText] = useState(null);

  useEffect(() => { onDirtyChange?.(s.dirty); }, [s.dirty, onDirtyChange]);

  const presets = s.value('presets') ?? [];
  const presetsFallback = s.fallback('presets') ?? [];
  const ocr = s.value('ocr');
  const pagesInvalid = pagesText !== null && !validPages(pagesText);
  const noPreset = s.status === 'ready' && presets.length === 0;
  const canSave = !pagesInvalid && !noPreset;
  const serverPages = s.fallback('max_pages');

  const togglePreset = (id) => {
    const on = presets.includes(id);
    s.set('presets', PRESETS.map((p) => p.id).filter((p) => (p === id ? !on : presets.includes(p))));
  };
  const typePages = (text) => {
    setPagesText(text);
    if (validPages(text)) s.set('max_pages', Number(text));
  };
  const resetRow = (name) => {
    if (name === 'max_pages') setPagesText(null);
    s.useServer(name);
  };
  const discard = () => { setPagesText(null); s.discard(); };
  const save = async () => {
    if (!(await s.save())) return;
    setPagesText(null);
    showToast?.('Document limits saved. They apply from the next conversion.', 4000);
  };

  const row = (name, id, label, extra = {}) => ({
    theme, id, label, source: s.source(name), envName: s.envName(name),
    canUseServer: s.canUseServer(name), onUseServer: () => resetRow(name), ...extra,
  });
  const describedBy = (id, ...more) => [`${id}-help`, `${id}-source`, ...more].filter(Boolean).join(' ');
  const lockedNote = (id, env) => (
    <span id={id} className="pl-5 text-[10px] text-amber-600">Turned off by the server setting <code>{env}</code>.</span>
  );
  const optionClass = (locked) => `flex items-center gap-2 text-xs ${theme.text} ${locked ? 'opacity-60' : 'cursor-pointer'} min-h-11 md:min-h-0`;
  const box = 'h-3 w-3 accent-blue-500';

  const docling = s.info.find((r) => r.id === 'docling');
  const pdfMax = s.info.find((r) => r.id === 'pdf_upload_max');
  const textMax = s.info.find((r) => r.id === 'text_upload_max');

  const convertBy = s.value('convert_by');
  const convertEnv = s.envName('convert_by');
  const readersLocked = s.fallback('convert_by') === 'role';

  const switchRow = (name, id, label, checkboxLabel, help) => {
    const locked = s.fallback(name) === false;
    const noteId = `${id}-locked`;
    return (
      <SettingRow {...row(name, id, label, { help, group: true })}>
        <div role="group" aria-labelledby={`${id}-label`} aria-describedby={describedBy(id)} className="flex flex-col gap-0.5">
          <label className={optionClass(locked || s.saving)}>
            <input
              type="checkbox" checked={!!s.value(name)} disabled={locked || s.saving}
              onChange={(e) => s.set(name, e.target.checked)}
              aria-describedby={locked ? noteId : undefined} className={box}
            />
            {checkboxLabel}
          </label>
          {locked && lockedNote(noteId, s.envName(name))}
        </div>
      </SettingRow>
    );
  };

  return (
    <section className="flex flex-col gap-4" aria-label="Documents">
      <h2 className={sectionTitle}>Documents</h2>
      {s.status === 'loading' && <p className={`text-xs ${theme.textMuted}`}>Loading…</p>}
      {s.status === 'failed' && <p className="text-xs text-red-500">Couldn&apos;t load the document settings.</p>}
      {s.status === 'ready' && (
        <>
          {docling?.value === 'Off' && (
            <p role="status" className="text-xs text-amber-600">
              Docling is off on this server (<code>{docling.env}</code>). These limits apply once it&apos;s on.
            </p>
          )}
          <div className={`flex flex-col gap-3 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
            <SettingRow {...row('convert_by', 'documents-convert-by', 'Who may convert', {
              help: 'Who can turn a PDF into Markdown with Docling.', group: true,
            })}>
              <div role="radiogroup" aria-labelledby="documents-convert-by-label"
                aria-describedby={describedBy('documents-convert-by')} className="flex flex-col gap-1">
                <div className="flex flex-col gap-0.5">
                  <label className={optionClass(readersLocked || s.saving)}>
                    <input
                      type="radio" name="documents-convert-by" value="readers"
                      checked={convertBy === 'readers'} disabled={readersLocked || s.saving}
                      onChange={() => s.set('convert_by', 'readers')}
                      aria-describedby={readersLocked ? 'documents-convert-by-locked' : undefined} className={box}
                    />
                    Everyone with the reader capability
                  </label>
                  {readersLocked && lockedNote('documents-convert-by-locked', convertEnv)}
                </div>
                <div className="flex flex-col gap-0.5">
                  <label className={optionClass(s.saving)}>
                    <input
                      type="radio" name="documents-convert-by" value="role"
                      checked={convertBy === 'role'} disabled={s.saving}
                      onChange={() => s.set('convert_by', 'role')}
                      aria-describedby="documents-convert-by-role-hint" className={box}
                    />
                    Only people with the Docling role
                  </label>
                  <span id="documents-convert-by-role-hint" className={`pl-5 text-[10px] ${theme.textSecondary}`}>
                    Give the role under People, or in Keycloak. Admins can always convert.
                  </span>
                </div>
              </div>
            </SettingRow>

            <SettingRow {...row('presets', 'documents-presets', 'Presets allowed', {
              help: 'Accurate runs a vision model on every page: the slowest.', group: true,
            })}>
              <div role="group" aria-labelledby="documents-presets-label"
                aria-describedby={describedBy('documents-presets')} className="flex flex-col gap-1">
                {PRESETS.map((p) => {
                  const locked = !presetsFallback.includes(p.id);
                  const noteId = `documents-presets-${p.id}-locked`;
                  const ocrNote = p.id === 'accurate' && ocr === false;
                  const described = [locked && noteId, ocrNote && 'documents-presets-ocr-note'].filter(Boolean).join(' ');
                  return (
                    <div key={p.id} className="flex flex-col gap-0.5">
                      <label className={optionClass(locked || s.saving)}>
                        <input
                          type="checkbox" checked={presets.includes(p.id)} disabled={locked || s.saving}
                          onChange={() => togglePreset(p.id)}
                          aria-describedby={described || undefined} className={box}
                        />
                        {p.label}
                      </label>
                      {locked && lockedNote(noteId, s.envName('presets'))}
                    </div>
                  );
                })}
                {ocr === false && (
                  <p id="documents-presets-ocr-note" className="text-[10px] text-amber-600">
                    Accurate runs OCR, so it&apos;s off while OCR is off.
                  </p>
                )}
                {noPreset && <p id="documents-presets-none" className="text-xs text-red-500">Keep at least one preset.</p>}
              </div>
            </SettingRow>

            {switchRow('ocr', 'documents-ocr', 'OCR', 'Allow OCR', 'Reads scanned pages. Slow on long scans.')}
            {switchRow('picture_description', 'documents-picture-description', 'Picture descriptions',
              'Allow picture descriptions', 'Runs a vision model on every picture.')}

            <SettingRow {...row('max_pages', 'documents-max-pages', 'Largest conversion', {
              help: 'Pages one conversion may take; 0 = no limit. A longer PDF can still be converted a page range at a time.',
            })}>
              <input
                id="documents-max-pages" type="number" min={0} step={1} inputMode="numeric"
                value={pagesText ?? s.value('max_pages') ?? ''} disabled={s.saving}
                onChange={(e) => typePages(e.target.value)}
                aria-invalid={pagesInvalid || undefined}
                aria-describedby={describedBy('documents-max-pages', serverPages > 0 && 'documents-max-pages-limit', pagesInvalid && 'documents-max-pages-error')}
                className={`w-32 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-h-11 md:min-h-0`}
              />
              {serverPages > 0 && (
                <p id="documents-max-pages-limit" className="text-[10px] text-amber-600">
                  The server setting <code>{s.envName('max_pages')}</code> allows at most {serverPages}.
                </p>
              )}
              {pagesInvalid && <p id="documents-max-pages-error" className="text-xs text-red-500">{PAGES_ERROR}</p>}
            </SettingRow>

            <SectionSaveBar theme={theme} dirty={s.dirty} saving={s.saving} error={s.error}
              canSave={canSave} onSave={save} onDiscard={discard} />
          </div>

          {pdfMax && textMax && (
            <p data-testid="upload-limits" className={`text-[10px] ${theme.textSecondary}`}>
              PDF uploads up to {pdfMax.value} and other files up to {textMax.value} (<code>{pdfMax.env}</code>, <code>{textMax.env}</code>).
              {' '}A proxy in front of the server can set a lower limit.
            </p>
          )}
        </>
      )}
    </section>
  );
}
