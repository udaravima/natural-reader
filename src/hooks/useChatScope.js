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

    // Pin a chat's project (null: none), whatever chat is on screen: a new
    // chat taking its session id, a fork taking its parent's, a refused first
    // send putting the choice back, a 404 clearing it.
    const hold = useCallback((sessionId, next) => setHeld({ sessionId, project: next }), []);

    return useMemo(() => ({ scope, project, setScope, hold }), [scope, project, setScope, hold]);
}
