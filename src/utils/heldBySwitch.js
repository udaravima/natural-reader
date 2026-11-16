// What a conversation would keep from `model`, worked out before the person sends: the same rule the server
// applies when it builds the prompt (server/chat/context.py, `_held` and `_held_replies`). A text file sent with
// its real values is for the model it was agreed for and no other, and neither are the replies of a model that
// read it.
//
// Returns { names, replies, agreedFor }: the held files' names, how many replies are held, and the models the
// real values were agreed for (the ones to switch back to). All empty when nothing is held.

const hasRealValues = (m) =>
    (m.attachments || []).some((a) => a && a.kind === 'text' && a.real_values === true);

export const heldBySwitch = (messages, model) => {
    const names = [];
    const agreedFor = [];
    const exposed = new Set();
    let replies = 0;
    if (!model) return { names, replies, agreedFor };
    for (const m of messages || []) {
        if (m.role === 'user') {
            if (!hasRealValues(m)) continue;
            exposed.add(m.model || '');
            if (m.model === model) continue;
            if (m.model && !agreedFor.includes(m.model)) agreedFor.push(m.model);
            for (const a of m.attachments) {
                if (a && a.kind === 'text' && a.real_values === true && !names.includes(a.name)) names.push(a.name);
            }
        } else if (m.role === 'assistant' && exposed.size > 0
            && (!m.model || (m.model !== model && exposed.has(m.model)))) {
            replies += 1;
        }
    }
    return { names, replies, agreedFor };
};
