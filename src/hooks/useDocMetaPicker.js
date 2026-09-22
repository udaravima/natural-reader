import { useState } from 'react';

/**
 * State for the optional per-document project/tags picker (the register/upload
 * surface — see registerDocument in ../lib/docMeta).
 *
 * The picker is *per-document*: it resets to empty whenever `docKey` changes
 * (pass the loaded document's identity, e.g. its file name). Without this, a
 * project/tag chosen for one document would silently carry over in the shared
 * picker and mis-attach to the next document the user indexes or converts.
 *
 * The reset happens during render via the previous-value tracker (React's
 * canonical "adjust state when a prop changes" pattern) rather than in an
 * effect: it resets synchronously before the render commits — no flash of the
 * old selection, and no race with the index/convert action that reads it.
 */
export function useDocMetaPicker(docKey) {
    const [projectId, setProjectId] = useState('');
    const [tagsText, setTagsText] = useState('');
    const [prevKey, setPrevKey] = useState(docKey);

    if (docKey !== prevKey) {
        setPrevKey(docKey);
        setProjectId('');
        setTagsText('');
    }

    return { projectId, setProjectId, tagsText, setTagsText };
}
