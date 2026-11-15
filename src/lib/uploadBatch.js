/**
 * The rules of a bulk upload that live in the browser (bulk upload spec, phase 2, "What the browser does"): which
 * picked files take part, their order, repeated names inside one batch (the server judges each file against the
 * project only and can't see the batch), each row's choices, and what each choice sends.
 *
 * A plan row is the server's answer for one file ({ name, action, document, version, candidates, code, message })
 * plus { path, size, file, repeat }. `action`: new | version | ambiguous | unchanged | older_version | refused.
 */
export const ACCEPTED_EXT = ['.pdf', '.md', '.markdown', '.txt'];
const PDF_MAGIC = [37, 80, 68, 70, 45]; // %PDF-

export function isHiddenPart(part) {
  return part.startsWith('.') || part === 'node_modules';
}

/** Any part of the path is hidden or node_modules: such files are left out without a row and don't count. */
export function isHiddenPath(path) {
  return String(path).split('/').some(isHiddenPart);
}

/** The name the server files under: the last path part, trimmed (server: bulk_upload.clean_name). */
export function baseName(name) {
  return String(name || '').replace(/\\/g, '/').split('/').pop().trim();
}

/** Names compare as the server compares them: lower(btrim(name)). */
export function nameKey(name) {
  return baseName(name).toLowerCase();
}

function extension(name) {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot).toLowerCase() : '';
}

const isPdfHead = (head) => PDF_MAGIC.every((b, i) => head?.[i] === b);

/**
 * Keep what can be uploaded, in path order. A file counts by its extension, or, with no extension at all, by its
 * first bytes (a scan saved as "scan"). Anything else is counted in `leftOut`, for one line on the screen.
 *
 * @param {Array<{file: File, path: string}>} items
 * @param {{ readHead: (file: File) => Promise<Uint8Array> }} io
 */
export async function collect(items, { readHead }) {
  const files = [];
  let leftOut = 0;
  for (const { file, path } of items) {
    if (isHiddenPath(path)) continue;
    const name = baseName(file.name);
    const ext = extension(name);
    if (ACCEPTED_EXT.includes(ext) || (!ext && !name.includes('.') && isPdfHead(await readHead(file)))) {
      files.push({ file, path, name });
    } else {
      leftOut += 1;
    }
  }
  files.sort((a, b) => a.path.localeCompare(b.path, 'en', { numeric: true }));
  return { files, leftOut };
}

/** Rows in path order: the second and later rows sharing a name are repeats (Skip by default). */
export function markRepeats(rows) {
  const seen = new Set();
  return rows.map((row) => {
    const key = nameKey(row.name);
    const repeat = seen.has(key);
    seen.add(key);
    return repeat ? { ...row, repeat: true } : row;
  });
}

function addedOn(at) {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

const SEPARATE = { id: 'separate', label: 'Separate document' };
const SKIP = { id: 'skip', label: 'Skip' };

/** A row's menu, its default first where the spec puts it; [] for a row with no real choice. */
export function choicesFor(row) {
  if (row.action === 'refused' || row.action === 'unchanged') return [];
  if (row.repeat) return [SKIP, SEPARATE];
  if (row.action === 'version') {
    return [{ id: 'version', label: `New version of ${row.document?.name ?? row.name}` }, SEPARATE, SKIP];
  }
  if (row.action === 'older_version') {
    return [{ id: 'leave', label: 'Leave alone' }, { id: 'restore', label: 'Restore as current' }];
  }
  if (row.action === 'ambiguous') {
    return [SKIP, ...(row.candidates || []).map((c) => ({
      id: `doc:${c.id}`,
      label: `v${c.version} · added by ${c.added_by?.name ?? 'someone no longer here'} · ${addedOn(c.added_at)}`,
    })), SEPARATE];
  }
  return [];
}

export function defaultChoice(row) {
  const choices = choicesFor(row);
  return choices.length ? choices[0].id : null;
}

/** The upload's fields for a row and its choice, or null when the row sends nothing. */
export function requestFor(row, choice) {
  if (row.action === 'refused' || row.action === 'unchanged' || choice === 'skip' || choice === 'leave') return null;
  if (choice?.startsWith('doc:')) return { on_match: 'version', on_older: 'leave', document_id: choice.slice(4) };
  return {
    on_match: choice === 'separate' ? 'separate' : 'version',
    on_older: choice === 'restore' ? 'restore' : 'leave',
  };
}

/** The plan's counts line and what Upload would send. `choices[i]` is row i's choice. */
export function summarize(rows, choices) {
  const out = { new: 0, versions: 0, restored: 0, unchanged: 0, leftAlone: 0, skipped: 0, refused: 0, toSend: 0,
    bytesToSend: 0 };
  rows.forEach((row, i) => {
    const choice = choices[i];
    if (row.action === 'refused') out.refused += 1;
    else if (row.action === 'unchanged') out.unchanged += 1;
    else if (choice === 'skip') out.skipped += 1;
    else if (choice === 'leave') out.leftAlone += 1;
    else if (choice === 'restore') out.restored += 1;
    else if (choice === 'separate' || row.action === 'new') out.new += 1;
    else out.versions += 1;
    if (requestFor(row, choice)) {
      out.toSend += 1;
      out.bytesToSend += row.size || 0;
    }
  });
  return out;
}
