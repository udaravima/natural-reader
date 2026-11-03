import { markdownToSpeech } from '../../utils/markdownToSpeech';

const BOUNDARY = /(?<=[.!?])\s+|\n\s*\n/;
const CLAUSE = /[,;:]\s+/g;
const SAYS_SOMETHING = /[\p{L}\p{N}]/u;

/**
 * Cuts a streaming reply into sentences, each as soon as it ends, because the first sound is what the person waits
 * for. `onSentence({ text, end })`: `text` is what to say, with the markdown gone; `end` is where it ends in the reply
 * as written, so a reply can be cut at what was heard. The time to that first sound grows with the first sentence, so
 * past `firstClauseChars` characters (0: never) the opening is cut at a comma instead of waiting for the full stop.
 */
export function createSentenceSplitter(onSentence, { firstClauseChars = 0 } = {}) {
  let buffer = '';
  let consumed = 0;   // characters of the reply before `buffer`
  let opening = firstClauseChars > 0;

  const emit = (raw) => {
    const text = markdownToSpeech(raw).trim();
    if (SAYS_SOMETHING.test(text)) {
      opening = false;
      onSentence({ text, end: consumed + raw.length });
    }
  };

  // The next cut: where the piece ends, and where the one after it starts.
  const nextCut = () => {
    const sentence = BOUNDARY.exec(buffer);
    const end = sentence && { end: sentence.index, next: sentence.index + sentence[0].length };
    if (!opening) return end;
    CLAUSE.lastIndex = firstClauseChars;
    const clause = CLAUSE.exec(buffer);
    if (!clause || (end && end.end <= clause.index)) return end;
    return { end: clause.index + 1, next: clause.index + clause[0].length };
  };

  return {
    feed(delta) {
      buffer += delta;
      for (let cut = nextCut(); cut; cut = nextCut()) {
        emit(buffer.slice(0, cut.end));
        consumed += cut.next;
        buffer = buffer.slice(cut.next);
      }
    },
    flush() {
      const raw = buffer.trimEnd();
      if (raw) emit(raw);
      consumed += buffer.length;
      buffer = '';
    },
  };
}
