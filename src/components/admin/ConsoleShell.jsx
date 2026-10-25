import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Shield } from 'lucide-react';

/**
 * The console's frame: a section list beside one panel, the way the Settings
 * window is laid out (SettingsDialog), but a full-height view rather than a
 * modal, so no backdrop and no focus trap. Below `md` the list becomes a
 * picker.
 *
 * `sections` is [{ id, label }]; the parent owns `active` and the section's
 * content (`children`). While `dirty` (a section has unsaved edits), leaving
 * through a tab, the picker or Back to reader asks first, inline above the
 * panel; Escape in the console means Keep editing.
 */
export function ConsoleShell({ theme, sections, active, onSelect, onBack, dirty = false, children }) {
  const rootRef = useRef(null);
  const keepRef = useRef(null);
  // What the admin tried to do while dirty: { go: 'tab', id } or { go: 'back' }.
  const [pending, setPending] = useState(null);

  const activeLabel = sections.find((s) => s.id === active)?.label ?? '';
  const tabEl = (id) => rootRef.current?.querySelector(`#admin-tab-${id}`);

  // The question opens with focus on its safe answer, so Escape and Enter both mean Keep editing.
  useEffect(() => { if (pending) keepRef.current?.focus(); }, [pending]);

  const goTo = (id) => {
    if (id === active) return;
    if (dirty) { setPending({ go: 'tab', id }); return; }
    onSelect(id);
  };

  const goBack = () => {
    if (dirty) { setPending({ go: 'back' }); return; }
    onBack();
  };

  const discard = () => {
    const p = pending;
    setPending(null);
    if (p.go === 'back') { onBack(); return; }
    onSelect(p.id);
    tabEl(p.id)?.focus();
  };

  const keepEditing = () => {
    setPending(null);
    tabEl(active)?.focus();
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape' && pending) { e.stopPropagation(); keepEditing(); }
  };

  const onTabKey = (e) => {
    const i = sections.findIndex((s) => s.id === active);
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    let to;
    if (step) to = sections[(i + step + sections.length) % sections.length];
    else if (e.key === 'Home') to = sections[0];
    else if (e.key === 'End') to = sections[sections.length - 1];
    else return;
    e.preventDefault();
    goTo(to.id);
    // Selection follows focus; when the guard held the move back, the
    // question takes focus instead.
    if (!dirty) tabEl(to.id)?.focus();
  };

  return (
    <div ref={rootRef} onKeyDown={onKeyDown} className={`h-full w-full flex flex-col ${theme.bg} ${theme.text}`}>
      <div className="flex items-center justify-between px-4 md:px-8 py-4">
        <h1 className="text-lg font-bold flex items-center gap-2">
          <Shield size={18} className="text-blue-500" />
          Admin console
        </h1>
        <button onClick={goBack} className="text-xs underline text-blue-500 flex items-center gap-1 min-h-11 md:min-h-0">
          <ArrowLeft size={12} /> Back to reader
        </button>
      </div>

      <div className="flex-1 min-h-0 flex flex-col md:flex-row">
        <nav aria-label="Admin" className={`shrink-0 md:w-52 px-4 md:px-2 pb-2 md:pb-0 md:border-r ${theme.border}`}>
          <select
            aria-label="Admin section"
            value={active}
            onChange={(e) => goTo(e.target.value)}
            className={`md:hidden w-full min-h-11 px-2 text-xs rounded border ${theme.border} ${theme.bgSecondary}`}
          >
            {sections.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <div role="tablist" aria-label="Admin sections" aria-orientation="vertical" onKeyDown={onTabKey}
            className="hidden md:flex md:flex-col gap-1">
            {sections.map((s) => (
              <button
                key={s.id}
                id={`admin-tab-${s.id}`}
                role="tab"
                aria-selected={s.id === active}
                aria-controls="admin-panel"
                tabIndex={s.id === active ? 0 : -1}
                onClick={() => goTo(s.id)}
                className={`text-left text-xs font-bold px-3 py-2 rounded-lg transition-colors whitespace-nowrap ${s.id === active ? 'bg-blue-600 text-white shadow' : `${theme.textSecondary} ${theme.hover}`}`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </nav>

        <div className="flex-1 min-h-0 min-w-0 overflow-y-auto">
          <div className="max-w-4xl mx-auto px-4 md:px-8 py-4 flex flex-col gap-4">
            {pending && (
              <div role="alertdialog" aria-modal="false" aria-labelledby="admin-unsaved-msg"
                className={`flex flex-wrap items-center gap-3 p-3 rounded-lg border border-amber-500/50 ${theme.bgSecondary} text-xs`}>
                <span id="admin-unsaved-msg">You have unsaved changes in {activeLabel}.</span>
                <button onClick={discard} className="underline text-red-500 min-h-11 md:min-h-0">Discard changes</button>
                <button ref={keepRef} onClick={keepEditing} className="underline text-blue-500 min-h-11 md:min-h-0">Keep editing</button>
              </div>
            )}
            <div id="admin-panel" role="tabpanel" aria-labelledby={`admin-tab-${active}`} className="flex flex-col gap-10">
              {children}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
