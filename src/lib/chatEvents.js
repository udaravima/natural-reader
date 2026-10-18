/**
 * Pure reducer: one server chat event -> the assistant message it updates
 * (spec §6, §8). No React, no I/O: the same fixtures the backend asserts are
 * replayed through it in tests, so a format change on either side fails.
 * Unknown event types return the message unchanged (forward compatible).
 */

// What the reply's status line says while a tool runs. A later round of the
// same answer is "Still searching… (round n)" (RAG spec §9).
const TOOL_STATUS = {
    search_documents: 'Searching the document…',
    read_document_pages: 'Reading the document…',
    web_search: 'Searching the web…',
    extract_from_documents: 'Reading pages…',
    document_outline: 'Reading an outline…',
    read_web_pages: 'Reading web pages…',
    offer_web_search: 'Offering a web search…',
};

export function toolStatusFor(toolName, round) {
    if (round > 1) return `Still searching… (round ${round})`;
    return TOOL_STATUS[toolName] || 'Running a tool…';
}

// A note the server saved with the reply, shown with it (ContextNotes, SourceChips).
const addNote = (msg, note) => ({ ...msg, docContext: { notes: [...(msg.docContext?.notes || []), note] } });

const settle = (msg, toolCallId, summary) => ({
    ...msg,
    toolCalls: (msg.toolCalls || []).map((tc) => (tc.id === toolCallId ? { ...tc, result_summary: summary } : tc)),
});

export function applyEvent(msg, ev) {
    switch (ev?.type) {
        case 'start':
            return { ...msg, id: ev.messageId, status: 'streaming' };
        case 'data-context':
            return { ...msg, docContext: { notes: ev.items || [] } };
        case 'data-sources': {
            // v2.4 Task D: the pages the answer drew on, saved with the reply
            // as the same note (orchestrator.py), so a reload shows the chips.
            // A project reply (C2.1) sends them per document instead.
            if (Array.isArray(ev.documents)) return addNote(msg, { kind: 'sources', documents: ev.documents });
            const { docId, docName, pages, used } = ev;
            return addNote(msg, { kind: 'sources', docId, docName, pages: pages || [], used: !!used });
        }
        case 'data-web-sources':
            // The web pages the answer read (any scope), saved as the same note.
            return addNote(msg, { kind: 'web_sources', pages: ev.pages || [] });
        case 'data-web-offer':
            // The model offers a web search it wasn't allowed to run. The
            // reply itself goes on to finish normally; the card that asks the
            // user is drawn from this note.
            return addNote(msg, { kind: 'web_offer', query: ev.query, reason: ev.reason });
        case 'data-notice':
            return { ...msg, notices: [...(msg.notices || []), ev.message] };
        case 'start-step':
            return { ...msg, toolStatus: undefined };
        case 'reasoning-delta':
            return { ...msg, thinking: (msg.thinking || '') + (ev.delta || '') };
        case 'text-start':
            // A later step's text is a new paragraph; the server saves the
            // same blank line between non-empty step texts (orchestrator.py).
            return msg.content ? { ...msg, content: `${msg.content}\n\n` } : msg;
        case 'text-delta':
            return { ...msg, content: (msg.content || '') + (ev.delta || '') };
        case 'tool-input-available':
            return {
                ...msg,
                toolStatus: toolStatusFor(ev.toolName, ev.round),
                toolCalls: [...(msg.toolCalls || []),
                    { id: ev.toolCallId, name: ev.toolName, arguments: ev.input || {}, result_summary: null }],
            };
        case 'tool-output-available':
            return settle(msg, ev.toolCallId, ev.output || {});
        case 'tool-output-error':
            return settle(msg, ev.toolCallId, { error: ev.errorText || 'Tool failed' });
        case 'finish':
            return {
                ...msg,
                status: ev.finishReason === 'aborted' ? 'aborted' : 'complete',
                finishReason: ev.finishReason,
                stats: ev.stats || msg.stats,
                toolStatus: undefined,
            };
        case 'error':
            return { ...msg, status: 'error', error: { code: ev.code, message: ev.message }, toolStatus: undefined };
        default:
            return msg;
    }
}
