import { describe, it, expect } from 'vitest';
import { NUM_CTX_OPTIONS, KEEP_ALIVE_OPTIONS, withinLimit, shownValue } from './inferenceLimits';

describe('inference limits in the Settings page (v2.4 Task F)', () => {
    it('offers only context sizes up to the limit', () => {
        expect(withinLimit(NUM_CTX_OPTIONS, 16384, 'numCtx').map(([v]) => v))
            .toEqual(['auto', '4096', '8192', '16384']);
        expect(withinLimit(NUM_CTX_OPTIONS, null, 'numCtx')).toEqual(NUM_CTX_OPTIONS);
    });

    it('offers only keep-alive times up to the limit, never "Always" under one', () => {
        expect(withinLimit(KEEP_ALIVE_OPTIONS, 1800, 'keepAlive').map(([v]) => v)).toEqual(['auto', '5m', '30m']);
        expect(withinLimit(KEEP_ALIVE_OPTIONS, null, 'keepAlive').map(([v]) => v)).toContain('-1');
    });

    it('shows a saved value above the limit as the largest one allowed', () => {
        const ctx = withinLimit(NUM_CTX_OPTIONS, 16384, 'numCtx');
        expect(shownValue('65536', ctx, 'numCtx')).toBe('16384');
        expect(shownValue('8192', ctx, 'numCtx')).toBe('8192');
        expect(shownValue('auto', ctx, 'numCtx')).toBe('auto');
        const ka = withinLimit(KEEP_ALIVE_OPTIONS, 1800, 'keepAlive');
        expect(shownValue('-1', ka, 'keepAlive')).toBe('30m');
        expect(shownValue('1h', ka, 'keepAlive')).toBe('30m');
    });
});
