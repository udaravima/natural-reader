import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(import.meta.url), '../..');
const page = readFileSync(resolve(root, 'index.html'), 'utf8');
const icons = [...page.matchAll(/<link rel="(?:icon|apple-touch-icon)"[^>]*href="\/([^"]+)"/g)].map((m) => m[1]);

describe('the tab icon', () => {
  it('is linked, and each file is there to be served', () => {
    expect(icons).toEqual(['favicon.svg', 'favicon-32.png', 'apple-touch-icon.png']);
    for (const file of icons) expect(existsSync(resolve(root, 'public', file)), file).toBe(true);
  });
});
