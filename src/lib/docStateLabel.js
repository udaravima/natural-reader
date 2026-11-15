// A document's indexing state in words a person reads (bulk upload spec, phase 1). `queued` and `queue_ahead` come
// from the server's lists and status route: a document with a job waiting in the queue, and how many jobs are
// ahead of it. A 'stored' document with no job is one nothing will index until someone presses Retry index.

const WORDS = {
  extracting: 'Reading',
  extracted: 'Indexing',
  indexing: 'Indexing',
  indexed: 'Indexed',
  failed: 'Failed',
};

export function docStateLabel({ state, queued, queue_ahead: ahead } = {}) {
  if (queued && ahead > 0 && state !== 'indexed' && state !== 'failed') return `Waiting to index (${ahead} ahead)`;
  if (state === 'stored') return queued ? 'Waiting to index' : 'Not indexed';
  return WORDS[state] ?? state;
}

const RUNNING = new Set(['extracting', 'extracted', 'indexing']);

// Still moving on its own: worth watching. A 'stored' document with no job isn't.
export const isMoving = (doc) => !!doc && (!!doc.queued || RUNNING.has(doc.state));
