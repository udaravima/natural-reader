// Extensions spoken with "dot" whatever their case (R15.1), after a stem of two characters or more (R15.5), so the
// degrees B.Com and M.Com keep their full stop while ASP.NET is "ASP dot NET". Any other 2–5 character extension
// counts only in lowercase, after any stem, so a missing space before a capital ("hours.The") stays a full stop.
const KNOWN_EXTENSIONS = new Set([
    'md', 'pdf', 'txt', 'doc', 'docx', 'csv', 'json', 'html', 'htm', 'xml', 'xls', 'xlsx', 'ppt', 'pptx', 'png',
    'jpg', 'jpeg', 'gif', 'svg', 'zip', 'py', 'js', 'jsx', 'ts', 'tsx', 'yml', 'yaml', 'toml', 'log', 'wav', 'mp3',
    'mp4', 'com', 'org', 'net', 'io', 'dev',
]);
// Words joined by single dots with no space: a file name, a web address, a version, an abbreviation. A match may
// only start where a run of letters and digits starts: unanchored, it is retried from every letter of a long run,
// which is quadratic (R15.5).
const DOTTED = /(?<![\p{L}\p{N}])[\p{L}\p{N}]+(?:\.[\p{L}\p{N}]+)+/gu;

const LONG_STEM = /^[\p{L}\p{N}]{2}/u;   // two characters or more: code points, as Python counts them

const isExtension = (word, stem) => /^[a-z][a-z0-9]{1,4}$/.test(word)
    || (LONG_STEM.test(stem) && KNOWN_EXTENSIONS.has(word.toLowerCase()));

// A voice reads Architecture.md as a sentence ending before "MD", so the dots of a file name or a web address are
// spoken "dot". A dot is spoken when the word after it is an extension, or when that word starts with a letter and
// its own dot was spoken: "example" is too long for an extension, but www.example.co.uk is one address. The walk
// goes from the right so a spoken dot carries left, never right ("example.com.Then" keeps its full stop).
// Abbreviations (e.g.), decimals (3.5), versions (v2.3.0) and a missing space before a capital keep their dots.
const speakDots = (run) => {
    const words = run.split('.');
    const parts = [words[words.length - 1]];   // right to left
    let spoken = false;
    for (let i = words.length - 1; i > 0; i--) {
        spoken = isExtension(words[i], words[i - 1]) || (spoken && /^\p{L}/u.test(words[i]));
        parts.push(spoken ? ' dot ' : '.', words[i - 1]);
    }
    return parts.reverse().join('');
};

// Emphasis never spans a line: these are what JavaScript's `.` doesn't match.
const LINE_END = /[\n\r\u2028\u2029]/g;
// One level of emphasis, bold then italic. A star pairs with the next star on its line. An underscore opens only
// where no letter or digit comes before it and closes only where none comes after it (CommonMark's intraword rule),
// so the underscores in room_number or my_file_name are never taken as emphasis.
const BOLD = { size: 2, mark: /\*\*|__/g, star: /\*\*/g, opens: /(?<![\p{L}\p{N}])__/uy, closes: /__(?![\p{L}\p{N}])/gu };
const ITALIC = { size: 1, mark: /[*_]/g, star: /\*/g, opens: /(?<![\p{L}\p{N}])_/uy, closes: /_(?![\p{L}\p{N}])/gu };

// Where the global regex `pattern` next matches in `s`, at or after `from` (`s.length`: nowhere). Asked with `from`
// never going back, so an answer still ahead is reused and each search starts past the last one.
const forwardSearch = (pattern, s) => {
    let found = -1;
    return (from) => {
        if (found < from) {
            pattern.lastIndex = from;
            found = pattern.exec(s)?.index ?? s.length;
        }
        return found;
    };
};

// Pairs one level of emphasis and keeps the text between the marks, exactly as
// /\*\*(.*?)\*\*|(?<![\p{L}\p{N}])__(.*?)__(?![\p{L}\p{N}])/gu replaced by '$1$2' would (and the same with single
// marks), but in linear time. That regex retries every unclosed `_word` up to the end of its line, which is
// quadratic on a long line, and the phone runs these rules on the event loop every call shares (R15.5).
const pairEmphasis = (s, { size, mark, star, opens, closes }) => {
    const nextMark = forwardSearch(mark, s);
    const nextStar = forwardSearch(star, s);
    const nextClose = forwardSearch(closes, s);
    const nextLineEnd = forwardSearch(LINE_END, s);
    let out = '';
    let copied = 0;
    for (let at = nextMark(0); at < s.length;) {
        const body = at + size;
        opens.lastIndex = at;
        let close = s.length;
        if (s[at] === '*') close = nextStar(body);
        else if (opens.test(s)) close = nextClose(body);
        if (close < nextLineEnd(body)) {
            out += s.slice(copied, at) + s.slice(body, close);
            copied = close + size;
            at = nextMark(copied);
        } else {
            at = nextMark(at + 1);
        }
    }
    return out + s.slice(copied);
};

// ![alt](url) and [text](url): the opener, and the least text between the brackets.
const IMAGE = { opener: /!\[/g, size: 2, minText: 0 };
const LINK = { opener: /\[/g, size: 1, minText: 1 };
const CLOSE_BRACKET = /\]/g;
const CLOSE_PAREN = /\)/g;

// Keeps the text of each image or link, exactly as /!\[([^\]]*)]\([^)]*\)/g and /\[([^\]]+)]\([^)]*\)/g replaced by
// '$1' would, but in linear time. The text runs to the first `]`, a `(` must follow it at once, and the address runs
// to the first `)`. Those regexes send every unclosed `[` on to the end of the text looking for them (R15.5).
const keepLinkText = (s, { opener, size, minText }) => {
    const nextOpener = forwardSearch(opener, s);
    const nextBracket = forwardSearch(CLOSE_BRACKET, s);
    const nextParen = forwardSearch(CLOSE_PAREN, s);
    let out = '';
    let copied = 0;
    for (let at = nextOpener(0); at < s.length;) {
        const text = at + size;
        const close = nextBracket(text);
        const end = s[close + 1] === '(' ? nextParen(close + 2) : s.length;
        if (close - text >= minText && end < s.length) {
            out += s.slice(copied, at) + s.slice(text, close);
            copied = end + 1;
            at = nextOpener(copied);
        } else {
            at = nextOpener(at + 1);
        }
    }
    return out + s.slice(copied);
};

// Line-start rules: white space, then a bullet, a number or a rule, all dropped. Written `^\s*tail`, they retried
// every line start inside a run of white space, which is quadratic on a run of newlines, and every one of those met
// the same tail where the white space ends. So the tail is optional: a match takes the whole run, and gives it back
// when no tail follows (dropAtLineStart). Same output, in linear time (R15.5).
const BULLET = /^(\s*)([-*+]\s+)?/gm;
const NUMBERED = /^(\s*)(\d+\.\s+)?/gm;
const RULE = /^(\s*)(([-*_])\3{2,}\s*$)?/gm;
const dropAtLineStart = (s, rule) => s.replace(rule, (_, space, tail) => (tail === undefined ? space : ''));

/**
 * Convert a markdown fragment into TTS-friendly plain text.
 * Keeps visible text + light punctuation; drops markup that would otherwise be
 * vocalized as "star star" / "hash". Technical words are read precisely:
 * Architecture.md is "Architecture dot md", room_number is "room underscore number".
 *
 * Used by chat read-aloud and its audio download (useChatEngine.js,
 * useTtsEngine.downloadTextAudio) and by Talk (src/lib/speech/sentences.js);
 * server/phone/sentences.py plain() is its port for phone calls, rule for rule.
 * Documents in the reader (PDF, .txt, Markdown) are split in src/utils/segmentation.js
 * and skip this.
 */
export function markdownToSpeech(md) {
    if (!md) return '';
    let s = md;
    // Drop fenced code blocks entirely — code doesn't read aloud well
    s = s.replace(/```[\s\S]*?```/g, ' ');
    // Inline code → keep the inner text
    s = s.replace(/`([^`]+)`/g, '$1');
    // Images ![alt](url) → keep alt text only
    s = keepLinkText(s, IMAGE);
    // Links [text](url) → keep visible text only
    s = keepLinkText(s, LINK);
    // Bold / italic — keep inner text (pairEmphasis)
    s = pairEmphasis(s, BOLD);
    s = pairEmphasis(s, ITALIC);
    // Underscores inside a word are spoken, each one: AB_12 → "AB underscore 12", foo__bar → "foo underscore underscore bar"
    s = s.replace(/(?<=[\p{L}\p{N}])_+(?=[\p{L}\p{N}])/gu, (run) => `${' underscore'.repeat(run.length)} `);
    // File names and web addresses → "dot" (speakDots). After emphasis, so __init__.py is "init dot py".
    s = s.replace(DOTTED, speakDots);
    // Strikethrough — keep inner text
    s = s.replace(/~~(.*?)~~/g, '$1');
    // Heading hashes → strip leading #s, append ":" so TTS pauses naturally
    s = s.replace(/^\s{0,3}(#{1,6})\s+(.+)$/gm, '$2:');
    // Blockquote markers
    s = s.replace(/^\s{0,3}>\s?/gm, '');
    // List bullets and numeric prefixes → drop marker, keep item text
    s = dropAtLineStart(s, BULLET);
    s = dropAtLineStart(s, NUMBERED);
    // Horizontal rules
    s = dropAtLineStart(s, RULE);
    // Stray dangling markers left over from partial-stream fragments — best effort.
    // Only consume single leftover *, _, # sequences that no longer have a partner.
    s = s.replace(/(^|\s)([*_~`#]{1,3})(\s|$)/g, '$1$3');
    // Collapse whitespace. A space run is tried from its start only: retried from every space, it was quadratic.
    s = s.replace(/(?<![ \t])[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    return s;
}
