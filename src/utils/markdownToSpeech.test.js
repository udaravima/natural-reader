import { describe, it, expect } from 'vitest';
import { markdownToSpeech } from './markdownToSpeech';

// What markdownToSpeech says is pinned by the shared cases (server/tests/fixtures/speech/sentences_cases.json). Here,
// how long it takes: the phone runs the same rules (server/phone/sentences.py plain()) on the event loop every call
// shares, so every rule must be linear in its input (R15.5), and a long degenerate reply mustn't freeze the page.
describe('markdownToSpeech takes linear time', () => {
    it.each([
        ['letters', 'a'.repeat(50000)],
        ['underscore words', ' _ab'.repeat(25000)],
        ['double underscore words', ' __ab'.repeat(20000)],
        ['a dotted chain (a guard, not a regression test)', 'ab.'.repeat(16667)],
        ['spaces', ' '.repeat(50000)],
        ['links with no closing bracket', '[a'.repeat(25000)],
        ['links with no closing parenthesis', '[a]('.repeat(37500)],
        ['images with no closing parenthesis', '![a]('.repeat(20000)],
        ['newlines', '\n'.repeat(20000)],
    ])('on %s', (_, text) => {
        const started = performance.now();
        markdownToSpeech(text);
        // Milliseconds. Quadratic rules took 180 to 1,400 on all but the dotted chain; linear ones take a few.
        expect(performance.now() - started).toBeLessThan(100);
    });
});
