import { describe, it, expect } from 'vitest';
import { splitSentences, chunkSentences, extractSelectionOrPage } from './text.js';

describe('splitSentences', () => {
    it('returns [] for empty/whitespace', () => {
        expect(splitSentences('')).toEqual([]);
        expect(splitSentences('   \n  ')).toEqual([]);
    });
    it('splits on . ! ? and trims', () => {
        expect(splitSentences('One. Two! Three?')).toEqual(['One.', 'Two!', 'Three?']);
    });
    it('keeps a final sentence with no terminal punctuation', () => {
        expect(splitSentences('No terminal punctuation here')).toEqual(['No terminal punctuation here']);
    });
    it('collapses whitespace/newlines', () => {
        expect(splitSentences('Hi.\n\n   There.')).toEqual(['Hi.', 'There.']);
    });
    it('does not drop text around decimals/numbers/phone numbers (regression)', () => {
        expect(splitSentences('The price is $3.99 today.')).toEqual(['The price is $3.99 today.']);
        expect(splitSentences('Call 1.800.555.1234 now.')).toEqual(['Call 1.800.555.1234 now.']);
    });
});

describe('chunkSentences', () => {
    it('groups into chunks of the given size, preserving order', () => {
        const s = Array.from({ length: 65 }, (_, i) => `s${i}`);
        const chunks = chunkSentences(s, 30);
        expect(chunks.map((c) => c.length)).toEqual([30, 30, 5]);
        expect(chunks[0][0]).toBe('s0');
        expect(chunks[2][4]).toBe('s64');
    });
    it('returns [] for empty input', () => {
        expect(chunkSentences([], 30)).toEqual([]);
    });
});

describe('extractSelectionOrPage', () => {
    it('returns the trimmed selection string in selection mode', () => {
        const doc = { getSelection: () => ({ toString: () => '  picked text  ' }) };
        expect(extractSelectionOrPage(doc, 'selection')).toBe('picked text');
    });
    it('reads article/main text in page mode', () => {
        document.body.innerHTML = '<nav>Menu</nav><main>Hello world. This is the body.</main>';
        expect(extractSelectionOrPage(document, 'page')).toBe('Hello world. This is the body.');
    });
    it('falls back to body when there is no article/main', () => {
        document.body.innerHTML = 'Just body text.';
        expect(extractSelectionOrPage(document, 'page')).toBe('Just body text.');
    });
});
