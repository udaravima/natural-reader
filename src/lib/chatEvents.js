/**
 * Pure reducer: one server chat event -> the assistant message it updates
 * (spec §6, §8). No React, no I/O: the same fixtures the backend asserts are
 * replayed through it in tests, so a format change on either side fails.
 * Unknown event types return the message unchanged (forward compatible).
 */
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
        case 'data-notice':
            return { ...msg, notices: [...(msg.notices || []), ev.message] };
        case 'start-step':
            return { ...msg, toolStatus: undefined };
        case 'reasoning-delta':
            return { ...msg, thinking: (msg.thinking || '') + (ev.delta || '') };
        case 'text-delta':
            return { ...msg, content: (msg.content || '') + (ev.delta || '') };
        case 'tool-input-available':
            return {
                ...msg,
                toolStatus: 'executing tool…',
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
