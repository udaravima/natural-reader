import { describe, it, expect } from 'vitest';
import { TEXT_ACCEPT } from './attachment';
import { TEXT_EXTENSIONS } from './textAttachment';

describe('TEXT_ACCEPT', () => {
    const entries = TEXT_ACCEPT.split(',');
    it('offers every extension isTextFile takes', () => {
        for (const ext of TEXT_EXTENSIONS) expect(entries).toContain(`.${ext}`);
    });
    it('offers the .env siblings, which a bare .env entry does not match', () => {
        for (const n of ['local', 'development', 'production', 'staging', 'test', 'example']) {
            expect(entries).toContain(`.env.${n}`);
        }
    });
});
