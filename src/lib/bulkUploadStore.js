/**
 * One bulk upload per project, kept outside React (bulk upload spec, phase 2, "What the browser does"). A project
 * page shows one tab at a time and opening a document leaves it, so a batch held in the tab's own state would lose
 * its progress the moment someone looked elsewhere, while its requests went on unseen. Here the batch carries on,
 * and the tab shows it again when it comes back.
 *
 * Phases: idle → reading (hashing, one file at a time) → planned (the server's answer, one choice per row) →
 * sending (two files at a time) → done. A row's status while sending: waiting | sending | done | failed | stopped.
 */
import { collect, defaultChoice, markRepeats, requestFor } from './uploadBatch';
import { canHash, digestFile, readHead } from './fileDigest';

const IN_FLIGHT = 2;
const LANDED_EVERY_MS = 1000;
export const SIGNED_OUT = 'You were signed out. Sign in, then drop the files again: what was sent is safe.';
export const ACCESS_LOST = 'You can no longer add documents to this project.';

const IDLE = Object.freeze({ phase: 'idle', rows: [], leftOut: 0, hashed: true, progress: null, summary: null,
  error: null });

// The person can no longer upload here: signed out, their role lowered, removed from the project (a 404 on a row
// that named no document; one that named a document may just have lost that document), or the capability revoked.
function losesAccess(err, fields) {
  if (err.status === 401) return true;
  if (err.status === 403) return err.code === 'insufficient_role' || err.code === 'missing_capability';
  return err.status === 404 && !fields?.document_id;
}

function summarize(rows) {
  const results = rows.filter((r) => r.status === 'done').map((r) => r.answer?.result);
  const count = (...names) => results.filter((x) => names.includes(x)).length;
  return {
    sent: results.length, created: count('created'), versions: count('version'), restored: count('restored'),
    unchanged: count('unchanged'), notFiled: count('skipped', 'left_older', 'ambiguous'),
    failed: rows.filter((r) => r.status === 'failed').length, stopped: rows.filter((r) => r.status === 'stopped').length,
  };
}

const askBeforeLeaving = (e) => { e.preventDefault(); e.returnValue = ''; };

function createBatch(projectId) {
  let state = IDLE;
  let api = null;
  let onLanded = null;
  let generation = 0; // a newer pick or a reset makes an older pick's late answers stale
  let queue = [];
  let inFlight = 0;
  let halted = false;
  let landTimer = null;
  let lastLanded = 0;
  const listeners = new Set();

  const set = (patch) => {
    state = { ...state, ...patch };
    listeners.forEach((l) => l());
  };
  const setRow = (i, patch) => set({ rows: state.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const stopWaiting = () => set({ rows: state.rows.map((r) => (r.status === 'waiting' ? { ...r, status: 'stopped' } : r)) });

  const callLanded = () => {
    lastLanded = Date.now();
    onLanded?.();
  };
  const landed = () => {
    if (landTimer) return; // a re-read is already due
    landTimer = setTimeout(() => { landTimer = null; callLanded(); },
      Math.max(0, lastLanded + LANDED_EVERY_MS - Date.now()));
  };

  const finish = () => {
    if (inFlight || queue.length) return;
    clearTimeout(landTimer);
    landTimer = null;
    window.removeEventListener('beforeunload', askBeforeLeaving);
    set({ phase: 'done', summary: summarize(state.rows) });
    callLanded();
  };

  const haltForAccess = (err) => {
    if (halted && state.error) return; // once
    halted = true;
    queue = [];
    stopWaiting();
    set({ error: err.status === 401 ? SIGNED_OUT : ACCESS_LOST });
  };

  async function send(i) {
    const row = state.rows[i];
    const fields = requestFor(row, row.choice);
    setRow(i, { status: 'sending', percent: 0, error: null });
    try {
      const answer = await api.uploadFile(projectId, row.file, fields, {
        onProgress: (percent) => { if (state.rows[i].percent !== percent) setRow(i, { percent }); },
      });
      setRow(i, { status: 'done', percent: 100, answer });
      landed();
    } catch (err) {
      if (losesAccess(err, fields)) {
        setRow(i, { status: 'stopped' });
        haltForAccess(err);
      } else {
        setRow(i, { status: 'failed', error: err.message });
      }
    }
  }

  function pump() {
    while (inFlight < IN_FLIGHT && queue.length && !halted) {
      const i = queue.shift();
      inFlight += 1;
      send(i).finally(() => { inFlight -= 1; pump(); finish(); });
    }
  }

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    configure(next) {
      api = next.api;
      onLanded = next.onLanded;
    },

    /** @param {Array<{file: File, path: string}>} items from folderDrop */
    async pick(items) {
      if (state.phase === 'sending' || state.phase === 'reading') return;
      const mine = ++generation;
      const { files, leftOut } = await collect(items, { readHead });
      if (mine !== generation) return;
      set({ ...IDLE, phase: 'reading', leftOut, hashed: canHash(), progress: { done: 0, total: files.length } });
      const digests = [];
      for (const f of files) { // one at a time: memory holds one file
        digests.push(await digestFile(f.file));
        if (mine !== generation) return;
        set({ progress: { done: digests.length, total: files.length } });
      }
      if (!files.length) {
        set({ phase: 'planned', progress: null });
        return;
      }
      let plan;
      try {
        plan = await api.uploadPlan(projectId, files.map((f, i) => ({
          name: f.name, size: f.file.size, sha256: digests[i].sha256, head: digests[i].head })));
      } catch (err) {
        if (mine === generation) set({ ...IDLE, error: err.message });
        return;
      }
      if (mine !== generation) return;
      const rows = markRepeats(plan.files.map((answer, i) => ({
        ...answer, path: files[i].path, size: files[i].file.size, file: files[i].file, status: null })));
      set({ phase: 'planned', progress: null, rows: rows.map((r) => ({ ...r, choice: defaultChoice(r) })) });
    },

    choose(i, choice) {
      if (state.phase === 'planned') setRow(i, { choice });
    },

    start() {
      if (state.phase !== 'planned') return;
      queue = state.rows.map((r, i) => (requestFor(r, r.choice) ? i : -1)).filter((i) => i >= 0);
      if (!queue.length) return;
      halted = false;
      set({ phase: 'sending', error: null, summary: null,
        rows: state.rows.map((r, i) => (queue.includes(i) ? { ...r, status: 'waiting' } : r)) });
      window.addEventListener('beforeunload', askBeforeLeaving);
      pump();
    },

    /** The files in flight finish; nothing more starts. */
    stop() {
      if (state.phase !== 'sending') return;
      halted = true;
      queue = [];
      stopWaiting();
      finish();
    },

    retry(i) {
      if (state.rows[i]?.status !== 'failed') return;
      halted = false;
      queue.push(i);
      setRow(i, { status: 'waiting', error: null });
      if (state.phase !== 'sending') {
        set({ phase: 'sending', error: null });
        window.addEventListener('beforeunload', askBeforeLeaving);
      }
      pump();
    },

    reset() {
      if (state.phase === 'sending') return;
      generation += 1;
      set(IDLE);
    },
  };
}

const batches = new Map();

export function batchFor(projectId) {
  if (!batches.has(projectId)) batches.set(projectId, createBatch(projectId));
  return batches.get(projectId);
}

/** Tests only: forget every batch. */
export function forgetBatches() {
  batches.clear();
}
