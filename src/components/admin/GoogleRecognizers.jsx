import { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

/**
 * Admin console: the Google recognizers people's speech goes to (spec 2026-11-04-google-recognizers). The list is
 * read from Google when this opens: each recognizer's id, model, languages and location. The admin ticks the ones to
 * use, in order: a clip goes to the first ticked one that lists its language, and Auto (with any language none
 * lists) to the first. Only a recognizer Google listed can be saved; nothing ticked, GOOGLE_SPEECH_RECOGNIZER applies.
 */

const URL = '/v1/admin/google-recognizers';

async function reasonOf(res) {
  try {
    const { detail } = await res.json();
    if (typeof detail?.message === 'string') return detail.message;
    if (typeof detail === 'string') return detail;
  } catch { /* not JSON */ }
  return `HTTP ${res.status}`;
}

const idOf = (name) => name.split('/').pop();
const describe = (r) => [r.id, r.model, r.languages.map((l) => l.name).join(', ') || 'every language', r.location]
  .filter(Boolean).join(' · ');

export function GoogleRecognizers({ theme, apiHost, apiPort, showToast, onSaved }) {
  const [listing, setListing] = useState(null);   // { recognizers, chosen, missing, problems, source, in_use }
  const [failed, setFailed] = useState('');
  const [saving, setSaving] = useState(false);

  // Read once when the section opens; an answer after it closes is dropped.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, URL);
        const out = res.ok ? { listing: await res.json() } : { failed: await reasonOf(res) };
        if (live) (out.listing ? setListing(out.listing) : setFailed(out.failed));
      } catch (e) {
        if (live) setFailed(e.message);
      }
    })();
    return () => { live = false; };
  }, [apiHost, apiPort]);

  const save = async (names) => {
    if (saving) return;
    const before = listing;
    setListing({ ...listing, chosen: names, missing: listing.missing.filter((n) => names.includes(n)) });
    setSaving(true);
    try {
      const res = await apiFetch(apiHost, apiPort, URL, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ names }),
      });
      if (!res.ok) {
        setListing(before);
        showToast?.(`That didn't work: ${await reasonOf(res)}`, 5000);
        return;
      }
      const view = await res.json();
      setListing((now) => ({ ...now, source: view.google?.source ?? now.source, in_use: view.google?.chosen ?? now.in_use }));
      onSaved?.(view);
    } catch (e) {
      setListing(before);
      showToast?.(`That didn't work: ${e.message}`, 5000);
    } finally {
      setSaving(false);
    }
  };

  if (failed) return <p role="alert" className="text-[10px] text-red-500">Google&apos;s recognizers: {failed}</p>;
  if (!listing) return <p className={`text-[10px] ${theme.textSecondary}`}>Asking Google for its recognizers…</p>;

  const { chosen } = listing;
  const byName = new Map(listing.recognizers.map((r) => [r.name, r]));
  // Ticked ones first, in their order; then the rest as Google listed them.
  const rows = [...chosen.filter((n) => byName.has(n)).map((n) => byName.get(n)),
    ...listing.recognizers.filter((r) => !chosen.includes(r.name))];
  const toggle = (name) => save(chosen.includes(name) ? chosen.filter((n) => n !== name) : [...chosen, name]);
  const moveUp = (name) => {
    const i = chosen.indexOf(name);
    const names = [...chosen];
    [names[i - 1], names[i]] = [names[i], names[i - 1]];
    save(names);
  };
  const fromEnv = listing.source === 'env' && chosen.length === 0 && (listing.in_use ?? []).length > 0;

  return (
    <div role="group" aria-label="Google recognizers" className="flex flex-col gap-1">
      <span className={`text-xs font-bold ${theme.text}`}>Google recognizers</span>
      <p className={`text-[10px] ${theme.textSecondary}`}>
        Read from Google just now. Tick the ones to use: a clip goes to the first ticked recognizer that lists its
        language, and Auto, with any language none lists, goes to the first. Recognizers are made in Google&apos;s console.
      </p>
      {fromEnv && (
        <p className={`text-[10px] ${theme.textSecondary}`}>
          In use: {idOf(listing.in_use[0])}, from the server setting GOOGLE_SPEECH_RECOGNIZER, until you tick one.
        </p>
      )}
      {listing.problems.map((p) => <p key={p} className="text-[10px] text-amber-600">{p}</p>)}
      {listing.missing.map((name) => (
        <div key={name} className="flex flex-wrap items-center gap-2 text-[10px] text-amber-600">
          <span>Ticked, but Google didn&apos;t list {idOf(name)}: deleted there, or its location didn&apos;t answer.</span>
          <button onClick={() => save(chosen.filter((n) => n !== name))} disabled={saving}
            aria-label={`Untick ${idOf(name)}`}
            className="min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline">
            Untick
          </button>
        </div>
      ))}
      {rows.length === 0 && listing.problems.length === 0 && (
        <p className={`text-[10px] ${theme.textSecondary}`}>Google lists no recognizers for this server&apos;s key.</p>
      )}
      <ul className="flex flex-col gap-1">
        {rows.map((r) => {
          const at = chosen.indexOf(r.name);
          return (
            <li key={r.name} className={`min-h-11 md:min-h-0 flex items-center gap-2 text-[10px] ${theme.text}`}>
              <label className="min-w-0 flex-1 flex items-center gap-2" title={r.codes?.join(', ')}>
                <input type="checkbox" checked={at >= 0} disabled={saving} onChange={() => toggle(r.name)}
                  className="h-3 w-3 accent-blue-500" />
                <span className="min-w-0 truncate">{describe(r)}</span>
              </label>
              {at === 0 && <span className={theme.textSecondary}>Auto</span>}
              {at > 0 && (
                <button onClick={() => moveUp(r.name)} disabled={saving} aria-label={`Move ${r.id} up`}
                  className="min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline">
                  Move up
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
