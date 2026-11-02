import { describe, it, expect } from 'vitest';
import { createSentenceSplitter } from './sentences';

const split = () => {
  const out = [];
  return { out, splitter: createSentenceSplitter((s) => out.push(s)) };
};

describe('createSentenceSplitter', () => {
  it('gives a sentence as soon as it ends, and where it ends in the reply', () => {
    const { out, splitter } = split();
    splitter.feed('Hello there. How');
    expect(out).toEqual([{ text: 'Hello there.', end: 12 }]);
    splitter.feed(' are you? Fine');
    expect(out[1]).toEqual({ text: 'How are you?', end: 25 });
  });

  it('flush gives what is left', () => {
    const { out, splitter } = split();
    splitter.feed('Fine');
    expect(out).toEqual([]);
    splitter.flush();
    expect(out).toEqual([{ text: 'Fine', end: 4 }]);
  });

  it('keeps a short reply, which read-aloud would drop', () => {
    const { out, splitter } = split();
    splitter.feed('Yes.');
    splitter.flush();
    expect(out).toEqual([{ text: 'Yes.', end: 4 }]);
  });

  it('speaks the words but counts the markdown, so a cut lands in the right place', () => {
    const { out, splitter } = split();
    splitter.feed('**Bold** words. Next one.');
    splitter.flush();
    expect(out).toEqual([{ text: 'Bold words.', end: 15 }, { text: 'Next one.', end: 25 }]);
  });

  it('a paragraph break ends a line with no full stop', () => {
    const { out, splitter } = split();
    splitter.feed('A heading\n\nBody text.');
    splitter.flush();
    expect(out.map((s) => s.text)).toEqual(['A heading', 'Body text.']);
  });

  describe('the opening of a reply', () => {
    const cutAt = (chars) => {
      const out = [];
      return { out, splitter: createSentenceSplitter((s) => out.push(s), { firstClauseChars: chars }) };
    };

    it('a long first sentence is cut at a comma, so the first sound comes sooner', () => {
      const { out, splitter } = cutAt(30);
      splitter.feed('Ancient Rome began as a small town in Italy, and grew into an empire. Next.');
      splitter.flush();
      expect(out).toEqual([
        { text: 'Ancient Rome began as a small town in Italy,', end: 44 },
        { text: 'and grew into an empire.', end: 69 },
        { text: 'Next.', end: 75 },
      ]);
    });

    it('it goes as soon as the comma arrives, without waiting for the full stop', () => {
      const { out, splitter } = cutAt(30);
      splitter.feed('Ancient Rome began as a small town in Italy, and');
      expect(out.map((s) => s.text)).toEqual(['Ancient Rome began as a small town in Italy,']);
    });

    it('a comma too early is left alone: a few words alone sound clipped', () => {
      const { out, splitter } = cutAt(30);
      splitter.feed('Yes, it is the capital of France and always was. Next');
      expect(out.map((s) => s.text)).toEqual(['Yes, it is the capital of France and always was.']);
    });

    it('only the first piece is cut', () => {
      const { out, splitter } = cutAt(30);
      splitter.feed('Short opening. Then a much longer sentence, with a comma well past the limit, runs on. ');
      expect(out.map((s) => s.text)).toEqual([
        'Short opening.', 'Then a much longer sentence, with a comma well past the limit, runs on.',
      ]);
    });

    it('nought turns it off', () => {
      const { out, splitter } = cutAt(0);
      splitter.feed('Ancient Rome began as a small town in Italy, and grew. ');
      expect(out.map((s) => s.text)).toEqual(['Ancient Rome began as a small town in Italy, and grew.']);
    });
  });

  it('skips what has nothing to say, but still counts it', () => {
    const { out, splitter } = split();
    splitter.feed('---\n\nAfter it.');
    splitter.flush();
    expect(out).toEqual([{ text: 'After it.', end: 14 }]);
  });
});
