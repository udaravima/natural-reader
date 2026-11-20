import { describe, it, expect } from 'vitest';
import sentenceCases from '../../../server/tests/fixtures/speech/sentences_cases.json';
import verdictCases from '../../../server/tests/fixtures/speech/verdict_cases.json';
import { createSentenceSplitter } from './sentences';
import { heardSpeech, isEcho } from './checks';

// The same files are read by server/tests/test_phone_text.py: a phone call runs Talk's text rules in Python, and
// these cases keep the two from drifting. Plain text only, because the server's markdown handling is simpler.

describe('shared sentence cases', () => {
  it.each(sentenceCases)('$name', ({ input, expect: want }) => {
    const out = [];
    const splitter = createSentenceSplitter((s) => out.push(s), { firstClauseChars: input.first_clause_chars });
    input.deltas.forEach((d) => splitter.feed(d));
    if (input.flush) splitter.flush();
    expect(out).toEqual(want);
  });
});

describe('shared verdict cases', () => {
  it.each(verdictCases)('$name', ({ fn, input, expect: want }) => {
    const got = fn === 'heard_speech' ? heardSpeech(input, input.limits) : isEcho(input.text, input.said);
    expect(got).toBe(want);
  });
});
