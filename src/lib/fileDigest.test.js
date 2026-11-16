import { describe, it, expect, afterEach, vi } from 'vitest';
import { digestFile, canHash } from './fileDigest';

// sha256("%PDF-1.7 hello")
const PDF = '%PDF-1.7 hello';

async function expected(text) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

describe('digestFile', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('gives the first five bytes in base64 and the sha256 when hashing exists', async () => {
    const out = await digestFile(new File([PDF], 'a.pdf'));
    expect(out.head).toBe('JVBERi0=');
    expect(out.sha256).toBe(await expected(PDF));
    expect(canHash()).toBe(true);
  });

  it('gives a short head for a file under five bytes', async () => {
    expect((await digestFile(new File(['ab'], 'a.md'))).head).toBe('YWI=');
  });

  it('gives no hash without crypto.subtle (plain HTTP)', async () => {
    vi.stubGlobal('crypto', {});
    expect(canHash()).toBe(false);
    const out = await digestFile(new File([PDF], 'a.pdf'));
    expect(out).toEqual({ sha256: null, head: 'JVBERi0=' });
  });
});
