import { useMemo, useRef, useState } from 'react';
import { LONG_LIST, hits } from '../lib/listSearch';

/**
 * Long lists, in one place. Up to LONG_LIST entries nothing changes: a list stays a list and a dropdown stays a
 * plain <select>. Past it, a search box appears, and:
 *
 * SearchableList holds rows of controls (Admin → Models): they scroll inside their own box, so the page around them
 * stays short and the Save below stays reachable.
 *
 * SearchableSelect keeps the native <select>, because a phone's own picker and the keyboard already work on it: the
 * box narrows the options, and while it holds text the select opens up as a short list of the matches. The value in
 * use always stays among the options, so narrowing can never change what is selected.
 */
const VISIBLE_MATCHES = 8;

function SearchBox({ theme, label, query, onChange, onKeyDown, disabled, inputRef }) {
  return (
    <input
      ref={inputRef} type="search" value={query} aria-label={label} placeholder={label} disabled={disabled}
      autoComplete="off" spellCheck={false}
      onChange={(e) => onChange(e.target.value)} onKeyDown={onKeyDown}
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
      <div className={`flex flex-col gap-1 max-h-64 overflow-y-auto p-1 rounded border ${theme.border}`}>
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

const drawn = (o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>;

/**
 * `options`: [{ value, label, group?, disabled?, pinned? }]. A `group` draws an <optgroup>; `pinned` keeps an option
 * through a search (Auto). Keep the array stable between renders on a very long list (the timezones). Everything
 * else goes on the <select>, which keeps its own label.
 */
export function SearchableSelect({
  theme, options, value, onChange, findLabel, wrapperClassName = '', disabled, ...selectProps
}) {
  const [query, setQuery] = useState('');
  const box = useRef(null);
  const list = useRef(null);
  const long = options.length > LONG_LIST;
  const shown = useMemo(
    () => (query
      ? options.filter((o) => o.pinned || String(o.value) === String(value) || hits(query, o.label, o.value))
      : options),
    [options, query, value],
  );
  const rendered = useMemo(() => grouped(shown).map(({ group, options: inGroup }) => (group
    ? <optgroup key={group} label={group}>{inGroup.map(drawn)}</optgroup>
    : inGroup.map(drawn))), [shown]);

  const select = (
    <select
      {...selectProps} ref={list} value={value} disabled={disabled}
      size={long && query ? Math.min(VISIBLE_MATCHES, Math.max(2, shown.length)) : undefined}
      onChange={(e) => onChange(e.target.value)}
      onClick={() => { if (query) setQuery(''); }}
      onKeyDown={(e) => {
        if (!query) return;
        if (e.key === 'Enter') { e.preventDefault(); setQuery(''); }
        if (e.key === 'Escape') { e.preventDefault(); setQuery(''); box.current?.focus(); }
      }}
    >
      {rendered}
    </select>
  );
  if (!long) return select;

  const boxKeys = (e) => {
    if (e.key === 'ArrowDown' && query) { e.preventDefault(); list.current?.focus(); }
    if (e.key === 'Escape' && query) { e.preventDefault(); setQuery(''); }
    if (e.key === 'Enter') {
      e.preventDefault();   // never submits a form around it
      const matches = shown.filter((o) => !o.pinned && !o.disabled && String(o.value) !== String(value));
      if (query && matches.length === 1) { onChange(matches[0].value); setQuery(''); }
    }
  };
  return (
    <div className={`flex flex-col gap-1 min-w-0 ${wrapperClassName}`}>
      <SearchBox theme={theme} label={findLabel} query={query} onChange={setQuery} onKeyDown={boxKeys}
        disabled={disabled} inputRef={box} />
      {select}
      {query && (
        <p aria-live="polite" className={`text-[10px] ${theme.textSecondary}`}>
          {shown.length} of {options.length}. Pick one below.
        </p>
      )}
    </div>
  );
}
