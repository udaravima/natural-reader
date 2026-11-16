import { useEffect, useSyncExternalStore } from 'react';
import { batchFor } from '../lib/bulkUploadStore';

/**
 * A project's bulk upload (lib/bulkUploadStore.js holds it, so it outlives this tab): pick files, read the plan,
 * change a row, send, stop, try a row again. `onLanded` is called as files land, at most once a second, and once
 * when the batch ends, so the document list can be re-read.
 *
 * @returns {{ phase, rows, leftOut, hashed, progress, summary, error, describe, pick, choose, setDescribe, start, stop,
 *   retry, reset }}
 */
export function useBulkUpload({ api, projectId, onLanded }) {
  const batch = batchFor(projectId);
  useEffect(() => {
    batch.configure({ api, onLanded });
    return () => batch.configure({ api, onLanded: null }); // the batch goes on; nobody here to re-read for
  }, [batch, api, onLanded]);
  const state = useSyncExternalStore(batch.subscribe, batch.getState);
  return { ...state, pick: batch.pick, choose: batch.choose, setDescribe: batch.setDescribe, start: batch.start,
    stop: batch.stop, retry: batch.retry, reset: batch.reset };
}
