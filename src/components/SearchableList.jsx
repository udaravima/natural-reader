import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';
import { LONG_LIST, hits } from '../lib/listSearch';

/**
 * Long lists, in one place. Up to LONG_LIST entries nothing changes: a list stays a list and a dropdown stays a
 * plain <select>. Past it:
 *
 * SearchableList holds rows of controls (Admin → Models): a search box above them, and they scroll inside their own
 * box, so the page around them stays short and the Save below stays reachable.
 *
 * SearchableSelect is one control that opens a popup with the search box at its top, already focused: open it and
 * type. The popup is drawn outside the panel it was opened from, so a narrow sidebar or a scrolling box can't squeeze
 * or clip it, but inside a dialog, so the dialog's focus trap and its screen-reader boundary still hold.
 */
const POPUP_WIDTH = 320;   // px at least: wider than a narrow panel, so a long model name fits
const POPUP_HEIGHT = 360;  // px at most
const POPUP_ROOM = 200;    // px: with less than this below the control, it opens upward if there's more up there
const POPUP_GAP = 4;       // px between the control and the popup
const POPUP_MARGIN = 8;    // px kept clear of the edge of the screen

function SearchBox({ theme, label, query, onChange, onKeyDown, disabled, inputRef, ...rest }) {
  return (
    <input
      ref={inputRef} type="search" value={query} aria-label={label} placeholder={label} disabled={disabled}
      autoComplete="off" spellCheck={false}
      onChange={(e) => onChange(e.target.value)} onKeyDown={onKeyDown} {...rest}
      className={`w-full min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`}
    />
  );
}

/**
 * `items` and `textOf(item)` say what is searched. `keep(item)` always shows a row, whatever the search: one holding
 * an error, so nothing hidden can hold Save off. `children(shown)` draws the rows.
 */
export function SearchableList({ theme, label, items, textOf, keep, disabled, children }) {
  const [query, setQuery] = useState('');
  if (items.length <= LONG_LIST) return children(items);
  const shown = query ? items.filter((item) => keep?.(item) || hits(query, textOf(item))) : items;
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <SearchBox theme={theme} label={label} query={query} onChange={setQuery} disabled={disabled} />
      {/* Block, not a flex column: a flex column this height squeezes its rows to fit, down to 0 px, and never scrolls. */}
      <div className={`space-y-1 max-h-64 overflow-y-auto p-1 rounded border ${theme.border}`}>
        {children(shown)}
        {shown.length === 0 && <p className={`text-xs ${theme.textSecondary}`}>Nothing matches.</p>}
      </div>
      <p aria-live="polite" className={`text-[10px] ${theme.textSecondary}`}>
        {query ? `${shown.length} of ${items.length}` : `${items.length} in the list`}
      </p>
    </div>
  );
}

function grouped(options) {
  const groups = [];
  for (const option of options) {
    const last = groups[groups.length - 1];
    if (last && last.group === option.group) last.options.push(option);
    else groups.push({ group: option.group, options: [option] });
  }
  return groups;
}

/** Where the popup goes for this control: under it, or above it when that's where the room is. */
function placement(control) {
  const box = control.getBoundingClientRect();
  const screenWidth = document.documentElement.clientWidth;
  const screenHeight = window.visualViewport?.height ?? window.innerHeight;   // what a phone's keyboard leaves
  const width = Math.min(Math.max(box.width, POPUP_WIDTH), screenWidth - 2 * POPUP_MARGIN);
  const left = Math.max(POPUP_MARGIN, Math.min(box.left, screenWidth - width - POPUP_MARGIN));
  const below = screenHeight - box.bottom - POPUP_GAP - POPUP_MARGIN;
  const above = box.top - POPUP_GAP - POPUP_MARGIN;
  const upward = below < POPUP_ROOM && above > below;
  const edge = upward ? { bottom: window.innerHeight - box.top + POPUP_GAP } : { top: box.bottom + POPUP_GAP };
  return { left, width, maxHeight: Math.min(POPUP_HEIGHT, upward ? above : below), ...edge };
}

function PopupSelect({ theme, options, value, onChange, findLabel, wrapperClassName, disabled, className, ...control }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);   // an index into `choices`
  const [place, setPlace] = useState(null);
  const [host, setHost] = useState(null);
  const trigger = useRef(null);
  const popup = useRef(null);
  const box = useRef(null);
  const listId = useId();

  const shown = useMemo(
    () => (query ? options.filter((o) => hits(query, o.label, o.value)) : options),
    [options, query],
  );
  const choices = useMemo(() => shown.filter((o) => !o.disabled), [shown]);
  const inUse = (o) => String(o.value) === String(value);
  const rowId = (o) => `${listId}-${encodeURIComponent(String(o.value))}`;
  const current = options.find(inUse);

  const close = useCallback((refocus = true) => {
    setOpen(false);
    setQuery('');
    if (refocus) trigger.current?.focus();
  }, []);

  const show = (typed = '') => {
    if (disabled) return;
    setHost(trigger.current.closest('[role="dialog"]') ?? document.body);
    setPlace(placement(trigger.current));
    setQuery(typed);
    setActive(typed ? 0 : Math.max(0, options.filter((o) => !o.disabled).findIndex(inUse)));
    setOpen(true);
  };

  const pick = (option) => {
    if (!inUse(option)) onChange(String(option.value));
    close();
  };

  useLayoutEffect(() => { if (open) box.current?.focus({ preventScroll: true }); }, [open]);

  const highlighted = open ? choices[active] : undefined;
  const highlightedId = highlighted ? rowId(highlighted) : undefined;
  useEffect(() => {
    if (highlightedId) document.getElementById(highlightedId)?.scrollIntoView?.({ block: 'nearest' });
  }, [highlightedId]);

  useEffect(() => {
    if (!open) return undefined;
    const follow = (e) => {
      if (popup.current?.contains(e.target)) return;   // its own list scrolling
      setPlace(placement(trigger.current));
    };
    const outside = (e) => {
      if (!popup.current?.contains(e.target) && !trigger.current?.contains(e.target)) close(false);
    };
    window.addEventListener('resize', follow);
    window.addEventListener('scroll', follow, true);
    window.visualViewport?.addEventListener('resize', follow);
    document.addEventListener('pointerdown', outside);
    return () => {
      window.removeEventListener('resize', follow);
      window.removeEventListener('scroll', follow, true);
      window.visualViewport?.removeEventListener('resize', follow);
      document.removeEventListener('pointerdown', outside);
    };
  }, [open, close]);

  const triggerKeys = (e) => {
    const typed = e.key.length === 1 && e.key !== ' ' && !e.ctrlKey && !e.metaKey && !e.altKey;
    if (!typed && !['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) return;
    e.preventDefault();
    e.stopPropagation();
    if (!open) show(typed ? e.key : '');
  };

  const boxKeys = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, choices.length - 1)); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    if (e.key === 'Enter') {
      e.preventDefault();   // never submits a form around it
      if (highlighted) pick(highlighted);
    }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }   // not the dialog around it
    if (e.key === 'Tab') close();
  };

  const row = (option) => {
    const selected = inUse(option);
    return (
      <div
        key={option.value} id={rowId(option)} role="option" aria-selected={selected}
        aria-disabled={option.disabled || undefined}
        onClick={() => { if (!option.disabled) pick(option); }}
        onMouseEnter={() => { if (!option.disabled) setActive(choices.indexOf(option)); }}
        className={`flex items-start gap-2 px-3 py-2 md:py-1.5 min-h-11 md:min-h-0 text-xs ${
          option.disabled ? 'opacity-50' : 'cursor-pointer'} ${option === highlighted ? 'bg-blue-500/15' : ''}`}
      >
        <Check size={14} aria-hidden="true" className={`shrink-0 text-blue-500 ${selected ? '' : 'invisible'}`} />
        <span className="min-w-0 break-words">{option.label}</span>
      </div>
    );
  };

  return (
    <div className={`min-w-0 w-full ${wrapperClassName}`}>
      <button
        type="button" role="combobox" data-own-keys {...control} ref={trigger} value={value} disabled={disabled}
        aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
        onClick={() => (open ? close() : show())} onKeyDown={triggerKeys}
        className={`${className ?? ''} flex w-full items-center justify-between gap-2 text-left`}
      >
        <span className="min-w-0 truncate">{current?.label ?? ''}</span>
        <ChevronDown size={14} aria-hidden="true" className="shrink-0 opacity-60" />
      </button>
      {open && host && createPortal(
        <div
          ref={popup} data-own-keys style={{ position: 'fixed', zIndex: 1000, ...place }}
          onMouseDown={(e) => { if (e.target !== box.current) e.preventDefault(); }}   // focus stays in the box
          className={`flex flex-col overflow-hidden rounded-lg border shadow-xl ${theme.border} ${theme.bg} ${theme.text}`}
        >
          <div className={`p-2 border-b ${theme.border}`}>
            <SearchBox
              theme={theme} label={findLabel} query={query} inputRef={box} onKeyDown={boxKeys}
              onChange={(text) => { setQuery(text); setActive(0); }}
              aria-controls={listId} aria-activedescendant={highlightedId}
            />
          </div>
          <div id={listId} role="listbox" aria-label={control['aria-label'] ?? findLabel}
            className="flex-1 min-h-0 overflow-y-auto py-1">
            {grouped(shown).map(({ group, options: inGroup }) => (group
              ? (
                <div key={group} role="group" aria-label={group}>
                  <div className={`sticky top-0 px-3 py-1 text-[10px] font-bold ${theme.bg} ${theme.textSecondary}`}>
                    {group}
                  </div>
                  {inGroup.map(row)}
                </div>
              )
              : inGroup.map(row)))}
            {shown.length === 0 && <p className={`px-3 py-2 text-xs ${theme.textSecondary}`}>Nothing matches.</p>}
          </div>
        </div>,
        host,
      )}
    </div>
  );
}

const drawn = (o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>;

/**
 * `options`: [{ value, label, group?, disabled? }]. A `group` draws a heading. `onChange` gets a string, as a native
 * select gives. Everything else goes on the control itself, which keeps its own label.
 */
export function SearchableSelect({
  theme, options, value, onChange, findLabel, wrapperClassName = '', disabled, ...control
}) {
  if (options.length > LONG_LIST) {
    return (
      <PopupSelect theme={theme} options={options} value={value} onChange={onChange} findLabel={findLabel}
        wrapperClassName={wrapperClassName} disabled={disabled} {...control} />
    );
  }
  return (
    <select {...control} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {grouped(options).map(({ group, options: inGroup }) => (group
        ? <optgroup key={group} label={group}>{inGroup.map(drawn)}</optgroup>
        : inGroup.map(drawn)))}
    </select>
  );
}
