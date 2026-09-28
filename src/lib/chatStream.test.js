import { describe, it, expect } from 'vitest';
import { readEvents, turnPath } from './chatStream';
import { bodyOf, loadFixture, sseText } from './chatFixtures.testutil';

const collect = async (body) => {
    const out = [];
    for await (const ev of readEvents(body)) out.push(ev);
    return out;
};

describe('chatStream', () => {
    it('encodes the session id into the turns path', () => {
        expect(turnPath('s-1/x')).toBe('/v1/chat/sessions/s-1%2Fx/turns');
    });

    it('reads every fixture event in order even when frames split across reads', async () => {
        for (const name of ['plain', 'tool_round', 'error', 'notice', 'context']) {
            const events = loadFixture(name);
            expect(await collect(bodyOf(sseText(events)))).toEqual(events);
        }
    });

    it('stops at [DONE] and ignores anything after it', async () => {
        const got = await collect(bodyOf(sseText([{ type: 'start' }], 'data: {"type":"late"}\n\n')));
        expect(got).toEqual([{ type: 'start' }]);
    });

    it('skips a malformed frame and tolerates CRLF line endings', async () => {
        const text = 'data: {nope\r\n\r\ndata: {"type":"text-delta","delta":"a"}\r\n\r\ndata: [DONE]\r\n\r\n';
        expect(await collect(bodyOf(text, 5))).toEqual([{ type: 'text-delta', delta: 'a' }]);
    });

    // Final review I4: the server sends `: keep-alive` comment frames during
    // silent phases so proxies don't cut the stream; they carry no event.
    it('ignores keep-alive comment frames, even split across reads', async () => {
        const text = 'data: {"type":"text-delta","delta":"a"}\n\n: keep-alive\n\n: keep-alive\n\n'
            + 'data: {"type":"text-delta","delta":"b"}\n\ndata: [DONE]\n\n';
        expect(await collect(bodyOf(text, 7))).toEqual([
            { type: 'text-delta', delta: 'a' }, { type: 'text-delta', delta: 'b' }]);
    });
});
