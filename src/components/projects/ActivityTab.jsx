import { useCallback, useEffect, useState } from 'react';
import { roleLabel } from '../../lib/projectRoles';

// eslint-disable-next-line react-refresh/only-export-components
export function timeAgo(at, now = Date.now()) {
  const s = Math.max(0, Math.round((now - new Date(at).getTime()) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(at).toLocaleDateString();
}

// One line per event (A0 §10). People appear by their current names; a
// deleted person reads "a former member"; a removed document keeps its name.
// eslint-disable-next-line react-refresh/only-export-components
export function describeEvent(ev) {
  const actor = ev.actor?.name || 'A former member';
  const subject = ev.subject?.name || 'a former member';
  const d = ev.details || {};
  const doc = ev.doc?.name || d.name || 'a document';
  const self = Boolean(ev.actor?.id) && ev.actor.id === ev.subject?.id;
  switch (ev.kind) {
    case 'project.created': return ev.subject ? `${actor} created the project for ${subject}` : `${actor} created the project`;
    case 'project.renamed': return `${actor} renamed the project from "${d.from}" to "${d.to}"`;
    case 'project.described': return `${actor} changed the description`;
    case 'member.added': return `${actor} added ${self ? 'themselves' : subject} as ${roleLabel(d.role)}`;
    case 'member.role_changed': return `${actor} changed ${self ? 'their own role' : subject} from ${roleLabel(d.from)} to ${roleLabel(d.to)}`;
    case 'member.removed': return `${actor} removed ${subject}`;
    case 'member.left': return `${actor} left the project`;
    case 'document.added': return `${actor} added "${doc}"`;
    case 'document.removed': return `${actor} removed "${doc}"`;
    default: return `${actor}: ${ev.kind}`;
  }
}

export default function ActivityTab({ theme, api, project }) {
  const [events, setEvents] = useState(null);
  const [next, setNext] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);

  // Every setState here runs after the await, so calling load() from the
  // effect never sets state synchronously inside it.
  const load = useCallback(async (before) => {
    try {
      const page = await api.events(project.id, before);
      setEvents((prev) => (before ? [...(prev || []), ...page.events] : page.events));
      setNext(page.next_before);
      setError(null);
    } catch (e) {
      setEvents((prev) => prev || []);
      setError(e.message);
    } finally {
      setLoadingMore(false);
    }
  }, [api, project.id]);

  useEffect(() => { load(null); }, [load]);

  if (events === null) return <p className={`text-xs ${theme.textMuted}`}>Loading…</p>;
  const alert = error && <p role="alert" className="text-xs text-red-500">{error}</p>;
  if (events.length === 0) return error ? alert : <p className={`text-xs ${theme.textMuted}`}>No activity yet.</p>;
  return (
    <div className="flex flex-col gap-2">
      <ul className="flex flex-col gap-1">
        {events.map((ev) => (
          <li key={ev.id} className="text-xs">
            {describeEvent(ev)} <span className={`text-[10px] ${theme.textMuted}`}>· {timeAgo(ev.at)}</span>
          </li>
        ))}
      </ul>
      {alert}
      {next && (
        <button onClick={() => { setLoadingMore(true); load(next); }} disabled={loadingMore}
          className="self-start text-xs underline disabled:opacity-50">
          Load more
        </button>
      )}
    </div>
  );
}
