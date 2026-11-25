import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { phoneApi } from '../../lib/phoneApi';
import { usePolling } from '../../lib/usePolling';
import { CallFacts, CallTranscript, RefreshControl } from './PhoneCallParts';

/**
 * Admin console: Calls. Every phone line's calls in one list, newest first, 25 at a time, each with its transcript to
 * open. The list, and an open transcript, read again about every 2 s while a call in view is in progress and about
 * every 10 s otherwise, so a new call appears by itself; only while the tab is visible, and never after the section
 * goes away.
 * Timings are written when a call ends, so a call in progress shows its words but not yet its timings.
 */

const PAGE = 25;
const FAST_MS = 2000;    // while a call in view is in progress
const IDLE_MS = 10000;   // otherwise, so a new call shows by itself

const CAP = 200;   // the newest calls shown, also the endpoint's page limit

export function CallsSection({ theme, apiHost, apiPort, showToast, zone = 'UTC' }) {
  const api = useMemo(() => phoneApi(apiHost, apiPort), [apiHost, apiPort]);
  const [calls, setCalls] = useState(null);
  const [next, setNext] = useState(null);       // the cursor of the page after the last one loaded
  const [failed, setFailed] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [more, setMore] = useState(false);
  const [atCap, setAtCap] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [open, setOpen] = useState(null);       // { id, messages, stats } of the transcript on show
  const [opening, setOpening] = useState(null);
  // One sequence for each kind of read: an answer older than the newest one asked for is dropped, so a slow read
  // never lands over a newer one, and `wanted` is the transcript the admin has open now, so a late answer for one they
  // closed (or left for another) is never shown.
  const listSeq = useRef(0);
  const openSeq = useRef(0);
  const wanted = useRef(null);
  const rows = useRef([]);        // what is on show, for the next read's size

  // Takes a page's rows as the list. At the cap the list stops: there is no More, and the section says so.
  const show = (calls, nextCursor) => {
    const capped = calls.length >= CAP && nextCursor != null;
    rows.current = calls;
    setCalls(calls);
    setNext(capped ? null : nextCursor);
    setAtCap(capped);
  };

  // Reads the newest max(25, rows shown) calls in one request and replaces the list with them, so a call that came
  // in, changed or was deleted since the last read shows as it is and the list is always an unbroken newest-first run.
  const load = useCallback(async () => {
    const n = (listSeq.current += 1);
    try {
      const page = await api.allCalls({ limit: Math.min(CAP, Math.max(PAGE, rows.current.length)) });
      if (n !== listSeq.current) return;
      show(page.calls, page.next);
      setFailed(null);
      setUpdatedAt(new Date());
    } catch (e) {
      if (n === listSeq.current) setFailed(e.message);
    }
  }, [api]);

  const readOpen = useCallback(async (id) => {
    const n = (openSeq.current += 1);
    const full = await api.call(id);
    if (n !== openSeq.current || wanted.current !== id) return;
    setOpen({ id, messages: full.messages, stats: Array.isArray(full.stats) ? full.stats : [] });
  }, [api]);

  useEffect(() => { load(); }, [load]);

  // One tick: the list, then the open transcript (it may have just ended, and then its timings are there).
  const tick = useCallback(async () => {
    await load();
    if (wanted.current) await readOpen(wanted.current).catch(() => {});
  }, [load, readOpen]);

  const live = (calls ?? []).some((c) => c.inProgress);
  usePolling(tick, true, live ? FAST_MS : IDLE_MS);

  const refresh = async () => {
    setRefreshing(true);
    try {
      await tick();
    } finally {
      setRefreshing(false);
    }
  };

  // More adds older rows: it never replaces what is on show, and a row already read keeps its newer state.
  const loadMore = async () => {
    setMore(true);
    try {
      const page = await api.allCalls({ limit: Math.min(PAGE, CAP - rows.current.length), before: next });
      const seen = new Set(rows.current.map((c) => c.id));
      show([...rows.current, ...page.calls.filter((c) => !seen.has(c.id))], page.next);
    } catch (e) {
      showToast?.(e.message, 5000);
    } finally {
      setMore(false);
    }
  };

  const toggle = async (c) => {
    if (open?.id === c.id || wanted.current === c.id) { wanted.current = null; setOpen(null); return; }
    wanted.current = c.id;
    setOpening(c.id);
    setOpen(null);
    try {
      await readOpen(c.id);
    } catch (e) {
      if (wanted.current === c.id) { wanted.current = null; showToast?.(e.message, 5000); }
    } finally {
      setOpening(null);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <h2 className={`text-sm font-bold ${theme.text}`}>Calls</h2>
        <RefreshControl onRefresh={refresh} busy={refreshing} updatedAt={updatedAt} zone={zone} theme={theme} />
      </div>
      <p className={`text-[10px] ${theme.textMuted}`}>
        Every phone line&apos;s calls, newest first. A call in progress updates here every couple of seconds, and new calls appear by themselves.
      </p>
      <p className={`text-[10px] ${theme.textMuted}`}>Timings appear when the call ends.</p>
      {failed && <p role="alert" className="text-xs text-red-500">Could not load the calls: {failed}</p>}
      {calls === null && !failed && <p className={`text-[10px] ${theme.textMuted}`}>Loading…</p>}
      {calls?.length === 0 && <p className={`text-[10px] ${theme.textMuted}`}>No calls yet.</p>}
      {calls?.map((c) => (
        <div key={c.id} data-call={c.id} className={`flex flex-col gap-1 p-2 rounded border ${theme.border} text-xs`}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-bold">{c.lineName}</span>
            <CallFacts call={c} zone={zone} />
            {c.transcript && (
              <button type="button" onClick={() => toggle(c)} disabled={opening === c.id} className="min-h-11 md:min-h-0 underline">
                Transcript
              </button>
            )}
          </div>
          {open?.id === c.id && <CallTranscript theme={theme} messages={open.messages} stats={open.stats} />}
        </div>
      ))}
      {atCap && <p className={`text-[10px] ${theme.textMuted}`}>Showing the newest {CAP} calls.</p>}
      {next && (
        <button type="button" onClick={loadMore} disabled={more} className="self-start min-h-11 md:min-h-0 underline text-xs">
          {more ? 'Loading…' : 'More'}
        </button>
      )}
    </div>
  );
}
