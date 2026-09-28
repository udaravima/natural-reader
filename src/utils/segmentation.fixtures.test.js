// This suite reads files by URL against import.meta.url; jsdom's URL
// implementation (this repo's default test environment) resolves relative
// URLs against a fake http://localhost document base instead of the file:
// base, breaking node:fs URL resolution. Force node so file: URLs resolve
// the way Node (and the reader's own bundler) resolve them.
// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { chunkLayout } from './segmentation';

// Shared with server/tests/test_extract.py: the Python extractor must produce
// these exact layouts. Regenerate ONLY when the reader's segmentation changes:
//   UPDATE_SEGMENTATION_FIXTURES=1 npx vitest run src/utils/segmentation.fixtures.test.js
const DIR = new URL('../../server/tests/fixtures/segmentation/', import.meta.url);
const TYPES = { txt: 'text', md: 'markdown' };

// Same decode the reader gets from FileReader.readAsText: UTF-8, BOM stripped.
const decode = (buf) => new TextDecoder('utf-8').decode(buf);

describe('segmentation parity fixtures', () => {
    const inputs = readdirSync(DIR).filter((f) => /\.(txt|md)$/.test(f));
    it('has inputs', () => expect(inputs.length).toBeGreaterThan(0));
    for (const name of inputs) {
        it(`${name} matches its committed layout`, () => {
            const ext = name.split('.').pop();
            const layout = chunkLayout(TYPES[ext], decode(readFileSync(new URL(name, DIR))));
            const expectedUrl = new URL(`${name}.expected.json`, DIR);
            if (process.env.UPDATE_SEGMENTATION_FIXTURES) {
                writeFileSync(expectedUrl, JSON.stringify(layout, null, 1) + '\n');
            }
            expect(layout).toEqual(JSON.parse(readFileSync(expectedUrl, 'utf-8')));
        });
    }
});
