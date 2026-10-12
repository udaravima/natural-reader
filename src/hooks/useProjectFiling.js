import { useCallback, useState } from 'react';

/**
 * Filing a library document into a project, with the name-match prompt (V1
 * spec §4.2, §6). `file(target)` files it; a name_exists / older_version
 * conflict opens the dialog, and the person's choice retries with
 * as_version_of or keep_both. `prompt(target, conflict)` opens the dialog
 * for a conflict found elsewhere (the reader's upload-then-link). Cancel
 * files nothing and reports nothing: the document stays in the library.
 * `dialog` is null, or NameMatchDialog's props without `theme`.
 * target: { projectId, projectName, docId, label }.
 */
export function useProjectFiling({ fileDoc, onFiled, onError }) {
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);

  const attempt = useCallback(async (target, choice) => {
    setBusy(true);
    try {
      const out = choice
        ? await fileDoc(target.projectId, target.docId, choice)
        : await fileDoc(target.projectId, target.docId);
      if (out?.conflict) {
        setPending({ target, conflict: out.conflict });
        return out;
      }
      setPending(null);
      onFiled?.(out?.filed ?? null, target);
      return out;
    } catch (e) {
      setPending(null);
      onError?.(e.message, target);
      return { error: e.message };
    } finally {
      setBusy(false);
    }
  }, [fileDoc, onFiled, onError]);

  const file = useCallback((target) => attempt(target), [attempt]);
  const prompt = useCallback((target, conflict) => setPending({ target, conflict }), []);

  const dialog = pending ? {
    projectName: pending.target.projectName,
    conflict: pending.conflict,
    busy,
    onChoose: (choice) => attempt(pending.target, choice),
    onCancel: () => setPending(null),
  } : null;

  return { file, prompt, busy, dialog };
}
