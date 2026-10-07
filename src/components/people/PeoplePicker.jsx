import { useEffect, useRef, useState } from 'react';
import { Search, Loader2 } from 'lucide-react';
import { useLatest } from '../../hooks/useLatest';

const LOOKUP_DEBOUNCE_MS = 250;
const MIN_CHARS = 2;

/**
 * Find a person to add or share with (A0 §4, §10). Type a name or a full
 * email address and pick from the results. The server decides who can be
 * found (USER_DIRECTORY_MODE); "Try their full email address" is advice that
 * works in every mode. `excludeIds` hides people already chosen (`excludedLabel` is shown when that is all the search found). `lookup`
 * resolves to [{id, name, username, email?, status}] or throws Error(notice).
 */
export default function PeoplePicker({ theme, lookup, onPick, excludeIds = [], label = 'Find a person', disabled = false, excludedLabel = 'Already added.' }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState(null); // null: nothing searched for the current text
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const lookupRef = useLatest(lookup);
  // Monotonic id: an older, slower answer must not overwrite a newer one.
  const reqId = useRef(0);

  useEffect(() => {
    const text = q.trim();
    const id = ++reqId.current;
    if (text.length < MIN_CHARS) return undefined;
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        const found = await lookupRef.current(text);
        if (id === reqId.current) { setResults(found); setError(null); }
      } catch (e) {
        if (id === reqId.current) { setResults([]); setError(e.message); }
      } finally {
        if (id === reqId.current) setBusy(false);
      }
    }, LOOKUP_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q, lookupRef]);

  const change = (value) => {
    setQ(value);
    if (value.trim().length < MIN_CHARS) { setResults(null); setError(null); setBusy(false); }
  };
  const pick = (person) => {
    onPick(person);
    change('');
  };
  const shown = (results || []).filter((p) => !excludeIds.includes(p.id));

  return (
    <div className="flex flex-col gap-1">
      <div className={`flex items-center gap-2 px-2 py-1 rounded border ${theme.border} ${theme.bg}`}>
        <Search size={12} className={theme.textSecondary} />
        <input
          value={q}
          onChange={(e) => change(e.target.value)}
          placeholder="Name or full email address"
          aria-label={label}
          disabled={disabled}
          className={`flex-1 bg-transparent text-xs outline-none ${theme.text}`}
        />
        {busy && <Loader2 size={12} className="animate-spin" />}
      </div>
      {error && <p role="alert" className="text-[10px] text-red-500">{error}</p>}
      {results !== null && !busy && !error && shown.length === 0 && (
        <p className={`text-[10px] ${theme.textMuted}`}>
          {results.length > 0 ? excludedLabel : q.includes('@') ? 'No account uses that email address.' : 'Nobody found. Try their full email address.'}
        </p>
      )}
      {shown.length > 0 && (
        <ul aria-label="People found" className={`flex flex-col rounded border ${theme.border}`}>
          {shown.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => pick(p)}
                disabled={disabled}
                className={`w-full flex items-center gap-2 px-2 py-1 text-left text-xs hover:text-blue-500 ${theme.text}`}
              >
                <span className="truncate">{p.name}</span>
                {p.username && <span className={`text-[10px] ${theme.textMuted}`}>@{p.username}</span>}
                {p.email && <span className={`text-[10px] ${theme.textMuted}`}>{p.email}</span>}
                {p.status === 'pending' && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">awaiting approval</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
