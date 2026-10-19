import { useCallback, useMemo, useState } from 'react';

/**
 * What a chat is about (C2.1 spec §6): no document, the open document, or a
 * project. The engine sends it with every turn (`project_id`, `doc_id`).
 *
 * Two stores feed it. The project is the server's: a chat's stored
 * `scopeProject`, from the session list (`activeSession`). Document vs. none
 * is `useDocUse`'s, which already keeps it per user and per chat. The third
 * piece is `held`: a project choice the server has not stored yet, because
 * the chat has no turn since the choice, or no session at all (a new chat).
 * It is keyed by the chat it was made in, so another chat never inherits it.
 *
 * `project.name` is null when the chat's project exists but the reader can no
 * longer see it (the server hides the name). Nothing here guesses: the next
 * turn still sends its id, and the server's 404 is what clears it.
 */
export function useChatScope({ docUse, activeSessionId, activeSession, hasDoc }) {
    // { sessionId: string | null, project: {id, name} | null }. sessionId null is the new chat.
    const [held, setHeld] = useState(null);

    const stored = activeSessionId ? (activeSession?.scopeProject ?? null) : null;
    const project = held && held.sessionId === activeSessionId ? held.project : stored;
    const documentOn = docUse.enabled && hasDoc;

    const scope = useMemo(() => {
        if (project) return { kind: 'project', project };
        return { kind: documentOn ? 'document' : 'none' };
    }, [project, documentOn]);

    // The picker's choice, for the chat on screen. Choosing the document or
    // none also leaves any project (held null: the next turn sends project_id null).
    const setScope = useCallback((next) => {
        if (next.kind === 'project') {
            setHeld({ sessionId: activeSessionId, project: next.project });
            return;
        }
        setHeld({ sessionId: activeSessionId, project: null });
        docUse.setEnabled(next.kind === 'document');
    }, [activeSessionId, docUse]);

    // Pin a chat's project (null: none), whatever chat is on screen: a new chat
    // clearing the last one's, "Ask about this project" holding one for the new
    // chat, an opened chat's stored project. Only the project changes; the
    // operations below change the document's choice with it.
    const hold = useCallback((sessionId, next) => setHeld({ sessionId, project: next }), []);

    // --- A turn's life. The two stores (project here, document in useDocUse) change together, in one place each. ---

    // What this chat has now, read once before a send, while its session id can
    // still change (a new chat, a fork): { project, docEnabled }.
    const read = useCallback((sessionId) => ({ project, docEnabled: docUse.enabledFor(sessionId) }), [project, docUse]);

    // A new chat, or a browser-only chat forked to the server, takes the scope
    // of the chat it was sent from (`from`, from read()).
    const adopt = useCallback((sessionId, from) => {
        docUse.adopt(sessionId, from.docEnabled);
        setHeld({ sessionId, project: from.project });
    }, [docUse]);

    // A refused first send: the new chat never existed, so its scope goes back
    // to being the person's pending choice (adopt() had reset the document's).
    const restore = useCallback((from) => {
        docUse.setEnabledFor(null, from.docEnabled);
        setHeld({ sessionId: null, project: from.project });
    }, [docUse]);

    // The server refused the chat's project (404) and cleared its stored scope:
    // mirror it, so the chat is a general one with no document. `sessionId`
    // null is the new chat.
    const clearProject = useCallback((sessionId) => {
        setHeld({ sessionId, project: null });
        docUse.setEnabledFor(sessionId, false);
    }, [docUse]);

    return useMemo(
        () => ({ scope, setScope, hold, read, adopt, restore, clearProject }),
        [scope, setScope, hold, read, adopt, restore, clearProject],
    );
}
