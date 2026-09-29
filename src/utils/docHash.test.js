import { describe, it, expect, beforeEach } from 'vitest';
import { getOrComputeDocHash, forgetDocHash, clearDocHashCache, sha256Hex } from './docHash';

const buf = (s) => new TextEncoder().encode(s).buffer;

describe('docHash cache', () => {
    beforeEach(() => clearDocHashCache());

    it('forgetDocHash drops a name, so new same-size bytes under it hash afresh', async () => {
        const a = await getOrComputeDocHash('paper.pdf', buf('AAAA'));
        forgetDocHash('paper.pdf');
        const b = await getOrComputeDocHash('paper.pdf', buf('BBBB'));
        expect(a).not.toBe(b);
        expect(b).toBe(await sha256Hex(buf('BBBB')));
    });
});
