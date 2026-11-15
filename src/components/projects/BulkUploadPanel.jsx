import { useState } from 'react';
import { choicesFor, summarize } from '../../lib/uploadBatch';

/**
 * A bulk upload on a project's Documents tab (bulk upload spec, phase 2, "What the person does and sees"): the plan
 * with one menu per row that has a real choice, then the sending list, then one summary line. `batch` is what
 * useBulkUpload returns; this component only shows it.
 */
const SENT_STATES = new Set(['waiting', 'sending', 'done', 'failed', 'stopped']);

function size(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  const mb = bytes / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** What a planned row will do, with its current choice. */
function willDo(row) {
  const doc = row.document;
  if (row.action === 'refused') return row.message;
  if (row.action === 'unchanged') return 'Unchanged: not sent';
  switch (row.choice) {
    case 'skip':
      if (row.repeat) return `Another file here is named ${row.name}: skipped`;
      if (row.action === 'ambiguous') return `${row.candidates.length} documents here are named ${row.name}: skipped`;
      return 'Skipped';
    case 'separate':
      if (row.repeat) return `New document, beside the other ${row.name}`;
      if (row.action === 'ambiguous') return `New document, beside the ${row.candidates.length} named ${row.name}`;
      return `New document, beside the existing ${doc?.name ?? row.name}`;
    case 'leave': return `Same as v${row.version} of ${doc.name}: left alone`;
    case 'restore': return `Same as v${row.version} of ${doc.name}: restored as current`;
    case 'version': return `New version (v${row.version}) of ${doc.name}`;
    default:
      if (row.choice?.startsWith('doc:')) {
        const c = row.candidates.find((x) => `doc:${x.id}` === row.choice);
        return `New version of the ${row.name} at v${c?.version}`;
      }
      return 'New document';
  }
}

/** What the server did with a sent row. */
function didDo(answer) {
  switch (answer?.result) {
    case 'created': return 'New document';
    case 'version': return `New version (v${answer.document?.version})`;
    case 'restored': return `Restored as v${answer.document?.version}`;
    case 'unchanged': return 'Unchanged';
    case 'left_older': return `Same as v${answer.older_version}: left alone`;
    case 'ambiguous': return 'Several documents here have this name: skipped';
    default: return 'Skipped';
  }
}

function countsLine(s) {
  return [
    s.new && `${s.new} new`, s.versions && plural(s.versions, 'new version', 'new versions'),
    s.restored && `${s.restored} restored`, s.unchanged && `${s.unchanged} unchanged`,
    s.leftAlone && `${s.leftAlone} left alone`, s.skipped && `${s.skipped} skipped`,
    s.refused && `${s.refused} can't be sent`,
  ].filter(Boolean).join(' · ');
}

function summaryLine(s) {
  const parts = [
    s.created && `${s.created} new`, s.versions && plural(s.versions, 'new version', 'new versions'),
    s.restored && `${s.restored} restored`, s.unchanged && `${s.unchanged} unchanged`,
    s.notFiled && `${s.notFiled} not filed`,
  ].filter(Boolean);
  return `${s.sent} sent${parts.length ? `: ${parts.join(', ')}` : ''}.`
    + (s.failed ? ` ${s.failed} failed.` : '') + (s.stopped ? ` ${s.stopped} not sent.` : '');
}

// One line per row; on a phone the name, what it becomes and the menu stack.
function Line({ theme, mark, name, children, aside }) {
  return (
    <li className="flex flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-2">
      <span className="flex min-w-0 items-center gap-1 sm:w-48 sm:shrink-0">
        <span aria-hidden="true" className="w-3 shrink-0">{mark}</span>
        <span className="truncate" title={name}>{name}</span>
      </span>
      <span className={`min-w-0 flex-1 ${theme.textSecondary}`}>{children}</span>
      {aside}
    </li>
  );
}

function SentRows({ theme, rows, retry }) {
  const waiting = rows.filter((r) => r.status === 'waiting').length;
  return (
    <ul className="flex flex-col gap-1">
      {rows.map((r, i) => {
        if (!SENT_STATES.has(r.status) || r.status === 'waiting') return null;
        if (r.status === 'done') return <Line key={r.path} theme={theme} mark="✓" name={r.name}>{didDo(r.answer)}</Line>;
        if (r.status === 'sending') return <Line key={r.path} theme={theme} mark="…" name={r.name}>Sending ({r.percent ?? 0}%)</Line>;
        if (r.status === 'stopped') return <Line key={r.path} theme={theme} mark="" name={r.name}>Not sent</Line>;
        return (
          <Line key={r.path} theme={theme} mark="!" name={r.name}
            aside={<button onClick={() => retry(i)} aria-label={`Try ${r.name} again`} className="self-start underline">Try again</button>}>
            <span className="text-red-500">{r.error}</span>
          </Line>
        );
      })}
      {waiting > 0 && <li className={theme.textMuted}>{waiting} waiting</li>}
    </ul>
  );
}

export default function BulkUploadPanel({ theme, project, batch }) {
  const { phase, rows, error } = batch;
  const [details, setDetails] = useState(null); // null: open when something failed
  const alert = error && <div role="alert" className="text-red-500">{error}</div>;
  const box = `flex flex-col gap-2 rounded border p-2 text-[11px] ${theme.border}`;

  if (phase === 'idle') return alert ? <div className={box}>{alert}</div> : null;
  if (phase === 'reading') {
    return <div className={box}><span>Reading files · {batch.progress?.done ?? 0} of {batch.progress?.total ?? 0}</span></div>;
  }

  if (phase === 'planned') {
    const counts = summarize(rows, rows.map((r) => r.choice));
    return (
      <div className={box}>
        <div className="flex flex-wrap items-baseline justify-between gap-1">
          <span className="font-medium">Upload to &quot;{project.name}&quot;</span>
          <span className={theme.textMuted}>
            {plural(rows.length, 'file', 'files')} · {counts.toSend ? `${size(counts.bytesToSend)} to send` : 'nothing to send'}
          </span>
        </div>
        {!batch.hashed && <p className={theme.textMuted}>Unchanged files can&apos;t be detected before sending on this connection.</p>}
        <ul className="flex flex-col gap-1">
          {rows.map((r, i) => {
            const choices = choicesFor(r);
            return (
              <Line key={r.path} theme={theme} mark="" name={r.path}
                aside={choices.length > 0 && (
                  <select value={r.choice ?? ''} onChange={(e) => batch.choose(i, e.target.value)}
                    aria-label={`What to do with ${r.path}`}
                    className={`max-w-full self-start px-1 py-0.5 rounded border sm:max-w-[14rem] ${theme.border} ${theme.bg}`}>
                    {choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                  </select>
                )}>
                <span className={r.action === 'refused' ? 'text-red-500' : ''}>{willDo(r)}</span>
              </Line>
            );
          })}
        </ul>
        {batch.leftOut > 0 && (
          <p className={theme.textMuted}>{plural(batch.leftOut, "file isn't", "files aren't")} PDF, Markdown or text: left out</p>
        )}
        {rows.some((r) => r.choice === 'separate') && (
          <p className={theme.textMuted}>
            A separate document shares its name with another here, so next time that file will ask which one it belongs to.
          </p>
        )}
        {rows.length > 0 && <p>{countsLine(counts)}</p>}
        <div className="flex justify-end gap-2">
          <button onClick={batch.reset} className="underline">Cancel</button>
          <button onClick={batch.start} disabled={counts.toSend === 0}
            className="px-2 py-0.5 rounded bg-blue-500 text-white disabled:opacity-50">Upload {counts.toSend}</button>
        </div>
      </div>
    );
  }

  if (phase === 'sending') {
    const sent = rows.filter((r) => SENT_STATES.has(r.status));
    return (
      <div className={box}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="font-medium">Uploading · {sent.filter((r) => r.status === 'done').length} of {sent.length}</span>
          <button onClick={batch.stop} className="underline">Stop</button>
        </div>
        {alert}
        <SentRows theme={theme} rows={rows} retry={batch.retry} />
      </div>
    );
  }

  // done
  const s = batch.summary;
  const open = details ?? (s?.failed > 0);
  return (
    <div className={box}>
      {alert}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span>{s && summaryLine(s)}</span>
        <span className="flex gap-2">
          {rows.length > 0 && (
            <button onClick={() => setDetails(!open)} className="underline">{open ? 'Hide details' : 'Show details'}</button>
          )}
          <button onClick={batch.reset} className="underline">Dismiss</button>
        </span>
      </div>
      {open && <SentRows theme={theme} rows={rows} retry={batch.retry} />}
    </div>
  );
}
