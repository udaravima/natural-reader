import { useEffect, useRef } from 'react';

/**
 * Filing a file a project already knows (V1 spec §4.2, §6): by name
 * (`name_exists`) or by its exact bytes (`older_version`). One in-app dialog
 * for all three filing paths — never window.confirm. The choice is what the
 * caller retries with: { asVersionOf } or { keepBoth: true }.
 */
export default function NameMatchDialog({ theme, projectName, conflict, busy = false, onChoose, onCancel }) {
  const doc = conflict.document || {};
  const older = conflict.error === 'older_version';
  const first = useRef(null);
  useEffect(() => { first.current?.focus(); }, []);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Already in this project"
        onKeyDown={(e) => { if (e.key === 'Escape' && !busy) onCancel(); }}
        className={`w-full max-w-sm flex flex-col gap-3 p-4 rounded-lg border ${theme.border} ${theme.bg}`}
      >
        <p className="text-sm">
          {older
            ? `This file is an older version (v${conflict.version}) of ${doc.name} in ${projectName}.`
            : `A document called ${doc.name} is already in ${projectName} (v${doc.version}).`}
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          <button ref={first} disabled={busy} onClick={() => onChoose({ asVersionOf: doc.id })}
            className="px-3 py-1 text-xs rounded bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50">
            {older ? `Restore v${conflict.version} as current` : 'Add as new version'}
          </button>
          <button disabled={busy} onClick={() => onChoose({ keepBoth: true })}
            className={`px-3 py-1 text-xs rounded border ${theme.border} disabled:opacity-50`}>
            Keep both
          </button>
          <button disabled={busy} onClick={onCancel} className={`px-3 py-1 text-xs underline ${theme.textSecondary}`}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
