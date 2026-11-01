/**
 * Reading and masking text files for chat attachments. Pure, browser-side:
 * masking runs BEFORE anything leaves the browser (spec §3.2).
 */

export const TEXT_FILE_MAX_BYTES = 65536;
export const TEXT_MESSAGE_MAX_BYTES = 131072;

const TEXT_EXTENSIONS = new Set([
    'txt', 'md', 'env', 'json', 'yaml', 'yml', 'toml', 'ini', 'conf', 'cfg',
    'csv', 'tsv', 'log', 'xml', 'html', 'css', 'js', 'ts', 'jsx', 'tsx', 'py',
    'sh', 'sql', 'properties',
]);

export const isTextFile = (file) => {
    const name = (file?.name || '').toLowerCase();
    if (name.startsWith('.env')) return true;
    const dot = name.lastIndexOf('.');
    if (dot >= 0 && TEXT_EXTENSIONS.has(name.slice(dot + 1))) return true;
    return (file?.type || '').startsWith('text/');
};

export const readTextFile = async (file) => {
    const notText = { ok: false, error: `${file.name} isn't a text file` };
    if (file.size > TEXT_FILE_MAX_BYTES) {
        return { ok: false, error: `${file.name} is over 64 KB` };
    }
    let bytes;
    try {
        bytes = new Uint8Array(await file.arrayBuffer());
    } catch {
        return { ok: false, error: `${file.name} couldn't be read` };
    }
    if (bytes.includes(0)) return notText;
    try {
        return { ok: true, text: new TextDecoder('utf-8', { fatal: true }).decode(bytes) };
    } catch {
        return notText;
    }
};

export const textToBase64 = (text) => {
    const bytes = new TextEncoder().encode(text);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
        bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin);
};

// ---------------------------------------------------------------- masking
//
// Detectors read the original text and report ranges; nothing rewrites the text
// until the one render at the end. A range is { start, end } over that text, plus
// `length` where the placeholder must report something else (an env value's quotes
// are covered but not counted) and `url` where it takes the URL form.

const maskToken = (n) => `<masked, ${n} chars>`;
const maskUrlToken = (n) => `<masked URL, ${n} chars>`;
const PLACEHOLDER = '<masked(?: URL)?, \\d+ chars>';
const IS_PLACEHOLDER_RE = new RegExp(`^${PLACEHOLDER}$`);
const PLACEHOLDER_AT_RE = new RegExp(PLACEHOLDER, 'y');

// An unterminated block (a partial paste) masks to the end of the text.
const PRIVATE_KEY_RE = /-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|$)/g;
// (?<!...) anchors keep these linear on long unbroken runs. A password that is
// already a placeholder still matches, so the key scanner leaves it alone.
const URL_PASSWORD_RE = new RegExp(
    `(?<![a-z0-9+.-])([a-z][a-z0-9+.-]*://[^\\s:/@"',]*:)(${PLACEHOLDER}|[^\\s/]+)(?=@)`,
    'gi',
);
const KEYWORD_RE = /password|passwd|secret|token|api[_-]?key|private[_-]?key/gi;
const OPERATOR_RE = /===|!==|==|!=|:=|=>|=|:(?!:)/y;
const TYPED_DEFAULT_RE = /[ \t]*[\w.[\]|]+[ \t]*=(?!=)[ \t]*(?=["'])/y;
const TOKEN_RE = new RegExp(
    '\\b(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}'
    + '|github_pat_[A-Za-z0-9_]{8,}|xox[abprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|nrp_[A-Za-z0-9_-]{8,})',
    'g',
);
const JWT_RE = /(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g;
const ENV_PLAIN_RE = /^(true|false|yes|no|on|off)$/i;
const PLAIN_WORDS_RE = /^(true|false|yes|no|on|off|null|none)$/i;

const KEY_CHAR_RE = /[\w.-]/;
const PREFIX_CHAR_RE = /[ \t#/*>-]/;

const rangesOf = (text, re) => [...text.matchAll(re)]
    .map((m) => ({ start: m.index, end: m.index + m[0].length }));

// Each private-key block as start -> end, for the walkers below to step over.
const blocksIn = (text) => new Map(rangesOf(text, PRIVATE_KEY_RE).map((r) => [r.start, r.end]));

// First index from `from` whose character matches `stop`, else the text's length.
// A line break, quote or `=` inside a private-key block belongs to the block.
const scanTo = (text, blocks, stop, from) => {
    let j = from;
    while (j < text.length) {
        if (blocks.has(j)) j = blocks.get(j);
        else if (stop.test(text[j])) return j;
        else j += 1;
    }
    return text.length;
};

// Where a quoted value ends: the matching quote on the same line, with `\x`
// and a doubled quote staying inside. Returns -1 if it never closes.
const closingQuote = (text, blocks, open) => {
    const q = text[open];
    for (let j = open + 1; j < text.length; j += 1) {
        const c = text[j];
        if (blocks.has(j)) j = blocks.get(j) - 1;
        else if (c === '\n' || c === '\r') return -1;
        else if (c === '\\') j += 1;
        else if (c === q) {
            if (text[j + 1] !== q) return j;
            j += 1;
        }
    }
    return -1;
};

// Index of the bracket that closes the one at `open`, stepping over quoted
// strings and counting nested brackets; -1 if it does not close on this line.
const matchingBracket = (text, blocks, open) => {
    let depth = 0;
    for (let j = open; j < text.length; j += 1) {
        const c = text[j];
        if (blocks.has(j)) j = blocks.get(j) - 1;
        else if (c === '\n' || c === '\r') return -1;
        else if (c === '"' || c === "'") {
            const q = closingQuote(text, blocks, j);
            if (q < 0) return -1;
            j = q;
        } else if (c === '[' || c === '{') depth += 1;
        else if (c === ']' || c === '}') {
            depth -= 1;
            if (depth === 0) return j;
        }
    }
    return -1;
};

// Where a bare mid-line value ends, walking on from `from`.
const bareEnd = (text, blocks, from, quotedKey) => {
    if (quotedKey) return scanTo(text, blocks, /[\s,}\]]/, from);
    let end = scanTo(text, blocks, /\s/, from);
    while (end > from && /[,;)}\]]/.test(text[end - 1])) end -= 1;
    return end;
};

// Finds `key <op> value` where the key holds a secret-ish word and reports the
// value. Scanning resumes at the end of each value (never the end of the line),
// so later secrets on the same line are still found. `urls` are the URL rule's
// spans: a value starting exactly at one is that rule's job.
const keyRanges = (text, urls) => {
    const blocks = blocksIn(text);
    const ranges = [];
    let floor = 0;
    let nextUrl = 0;
    KEYWORD_RE.lastIndex = 0;
    let m;
    const resume = (at) => { KEYWORD_RE.lastIndex = at; floor = at; };
    while ((m = KEYWORD_RE.exec(text)) !== null) {
        let keyStart = m.index;
        while (keyStart > floor && KEY_CHAR_RE.test(text[keyStart - 1])) keyStart -= 1;
        let p = m.index + m[0].length;
        while (p < text.length && KEY_CHAR_RE.test(text[p])) p += 1;
        let quotedKey = false;
        if (text[p] === '"' || text[p] === "'") { quotedKey = true; p += 1; }
        const keyEnd = p;
        while (text[p] === ' ' || text[p] === '\t') p += 1;
        OPERATOR_RE.lastIndex = p;
        const op = OPERATOR_RE.exec(text);
        if (!op) { resume(keyEnd); continue; }
        p += op[0].length;
        if (op[0] === ':') {
            TYPED_DEFAULT_RE.lastIndex = p;
            const typed = TYPED_DEFAULT_RE.exec(text);
            if (typed) p += typed[0].length;
        }
        while (text[p] === ' ' || text[p] === '\t') p += 1;
        const opEnd = p;

        while (nextUrl < urls.length && urls[nextUrl].end <= opEnd) nextUrl += 1;
        if (nextUrl < urls.length && urls[nextUrl].start === opEnd) { resume(opEnd); continue; }

        let start = p;
        let end;
        let after;
        const close = (text[p] === '"' || text[p] === "'") ? closingQuote(text, blocks, p) : -1;
        if (close >= 0) {
            start = p + 1;
            end = close;
            after = close + 1;
        } else {
            let walk = keyStart;
            while (walk > floor && PREFIX_CHAR_RE.test(text[walk - 1])) walk -= 1;
            const lineOnly = walk === 0 || text[walk - 1] === '\n' || text[walk - 1] === '\r';
            if (lineOnly || text[p] === '"' || text[p] === "'") {
                end = scanTo(text, blocks, /[\n\r]/, p);
                while (end > start && /[ \t]/.test(text[end - 1])) end -= 1;
            } else {
                // A bracket (to its close, or the line's end) or a placeholder is read
                // whole, before any cut. Only a value that goes straight on keeps going:
                // `{Tr0ub}4dor` is one value, and `["a"],b: 1` stops at the bracket.
                let from = p;
                PLACEHOLDER_AT_RE.lastIndex = p;
                if (text[p] === '[' || text[p] === '{') {
                    const closeAt = matchingBracket(text, blocks, p);
                    from = closeAt >= 0 ? closeAt + 1 : scanTo(text, blocks, /[\n\r]/, p);
                } else if (PLACEHOLDER_AT_RE.test(text)) from = PLACEHOLDER_AT_RE.lastIndex;
                const stops = from > p && /[\s,;)}\]]/.test(text[from] ?? ' ');
                end = stops ? from : bareEnd(text, blocks, from, quotedKey);
            }
            after = end;
        }
        resume(Math.max(after, opEnd));
        const value = text.slice(start, end);
        if (value && !IS_PLACEHOLDER_RE.test(value) && !PLAIN_WORDS_RE.test(value)) {
            ranges.push({ start, end });
        }
    }
    return ranges;
};

// Runs on a slice of the text; `offset` is where the slice starts in it. A slice
// must hold a private-key block whole or not at all (a line of envRanges' does).
const genericRanges = (slice, offset) => {
    const urls = [...slice.matchAll(URL_PASSWORD_RE)].map((m) => {
        const start = m.index + m[1].length;
        return { start, end: start + m[2].length };
    });
    return [
        ...urls.filter((r) => !IS_PLACEHOLDER_RE.test(slice.slice(r.start, r.end))),
        ...keyRanges(slice, urls),
        ...rangesOf(slice, TOKEN_RE),
        ...rangesOf(slice, JWT_RE),
    ].map((r) => ({ start: r.start + offset, end: r.end + offset }));
};

// One word, then a separator: the only shape a line may have to keep its head.
const ENV_WORD_HEAD_RE = new RegExp(`^\\s*(?:set\\s+)?(?:${PLACEHOLDER}|[^\\s=:]+)[ \\t]*[=:]`);
const ENV_HEAD_RE = /^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*[ \t]*=/;
// Content detection is strict: `key = "x"` in source code is not an env file.
const ENV_DETECT_RE = /^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*=/;
const ENV_DETECT_SPACED_RE = /^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*\s*=/;
const ENV_URL_RE = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//;
const SPACED_CONFIG_EXT = /\.(ini|toml|cfg|conf|properties)$/;
// A control character (but tab) or a line separator inside a comment means it is not one line.
const NOT_ONE_LINE_RE = /[\p{Zl}\p{Zp}]|[^\P{Cc}\t]/u;

const baseName = (name) => (name || '').toLowerCase().split(/[\\/]/).pop();
const isEnvName = (name) => {
    const base = baseName(name);
    return base === '.env' || base.startsWith('.env.') || base.endsWith('.env');
};

const looksLikeEnv = (name, text) => {
    const re = SPACED_CONFIG_EXT.test(baseName(name)) ? ENV_DETECT_SPACED_RE : ENV_DETECT_RE;
    const lines = text.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
    if (!lines.length) return false;
    return lines.filter((l) => re.test(l)).length / lines.length >= 0.6;
};

// A block doesn't stop a comment being one line. Its place stays filled, so a
// control character just before it is still inside the line, not trimmed away.
const isComment = (body) => body.trimStart().startsWith('#')
    && !NOT_ONE_LINE_RE.test(body.replace(PRIVATE_KEY_RE, '-').trim());

// failClosed (env by NAME): a line that does not parse as KEY=value is masked
// after its one-word head and separator, or whole; a quote that never closes runs to the end.
const envRanges = (text, failClosed) => {
    const blocks = blocksIn(text);
    const lastBlockEnd = Math.max(0, ...blocks.values());
    const fileEnd = text.endsWith('\n') && lastBlockEnd < text.length ? text.length - 1 : text.length;
    const ranges = [];
    let next = 0;
    for (let lineStart = 0; lineStart < text.length; lineStart = next) {
        const lineBreak = scanTo(text, blocks, /\n/, lineStart);
        next = lineBreak + 1;
        const bodyEnd = lineBreak > lineStart && text[lineBreak - 1] === '\r' ? lineBreak - 1 : lineBreak;
        const body = text.slice(lineStart, bodyEnd);
        if (body.trim() === '') continue;
        if (isComment(body)) { ranges.push(...genericRanges(body, lineStart)); continue; }
        let headEnd;
        const trimmed = body.trim();
        const base64Tail = failClosed && /^[A-Za-z0-9+/]{16,}={1,2}$/.test(trimmed)
            && /[a-z]/.test(trimmed) && /[A-Z0-9]/.test(trimmed);
        const hm = base64Tail ? null : ENV_HEAD_RE.exec(body);
        if (hm) {
            headEnd = lineStart + hm[0].length;
        } else if (failClosed) {
            const head = base64Tail ? null : ENV_WORD_HEAD_RE.exec(body);
            headEnd = lineStart + (head ? head[0].length : body.length - body.trimStart().length);
        } else {
            ranges.push(...genericRanges(body, lineStart));
            continue;
        }
        const rest = text.slice(headEnd, bodyEnd);
        const v = rest.trim();
        if (v === '') continue;
        const start = headEnd + rest.length - rest.trimStart().length;
        let end = start + v.length;
        let inner = v;
        let multiline = false;
        const q = v[0];
        const quoted = q === '"' || q === "'";
        const closed = quoted && v.length >= 2 && v.endsWith(q);
        const close = quoted && !closed ? scanTo(text, blocks, q === '"' ? /"/ : /'/, start + 1) : -1;
        if (closed) {
            inner = v.slice(1, -1);
        } else if (close >= end) {
            // opens a quote that does not close on this line: one value, to the
            // end of the line that closes it, or of the file if none does
            multiline = true;
            if (close < text.length) {
                const closeLine = scanTo(text, blocks, /\n/, close);
                next = closeLine + 1;
                end = text[closeLine - 1] === '\r' ? closeLine - 1 : closeLine;
            } else {
                next = text.length;
                end = fileEnd;
            }
            inner = text.slice(start + 1, text[end - 1] === q && end - 1 > start ? end - 1 : end);
        }
        if (!multiline && (inner === '' || IS_PLACEHOLDER_RE.test(inner) || ENV_PLAIN_RE.test(inner)
            || /^\d{1,6}$/.test(inner))) continue;
        ranges.push({ start, end, length: inner.length, url: ENV_URL_RE.test(inner) });
    }
    return ranges;
};

// Ranges that overlap become one; ranges that only touch stay apart. When one
// holds the other the outer range speaks for both; a partial overlap has no
// single voice, so it reports its span.
const merge = (ranges) => {
    const merged = [];
    for (const r of [...ranges].sort((a, b) => a.start - b.start || b.end - a.end)) {
        const last = merged[merged.length - 1];
        if (!last || r.start >= last.end) merged.push(r);
        else if (r.end > last.end) merged[merged.length - 1] = { start: last.start, end: r.end };
    }
    return merged;
};

const render = (text, ranges) => {
    let out = '';
    let copied = 0;
    for (const r of ranges) {
        const length = r.length ?? r.end - r.start;
        out += text.slice(copied, r.start) + (r.url ? maskUrlToken(length) : maskToken(length));
        copied = r.end;
    }
    return { text: out + text.slice(copied), masked: ranges.length };
};

export const maskText = (name, text) => {
    const keys = rangesOf(text, PRIVATE_KEY_RE);
    let ranges;
    const withTokens = (envs) => [...envs, ...rangesOf(text, TOKEN_RE), ...rangesOf(text, JWT_RE)];
    if (isEnvName(name)) ranges = withTokens(envRanges(text, true));
    // Lines count as they stand and as the masked file will read: a block's own lines
    // must not dilute detection, or masking the file again would pick the other mode.
    else if (looksLikeEnv(name, text) || looksLikeEnv(name, render(text, keys).text)) {
        ranges = withTokens(envRanges(text, false));
    } else ranges = genericRanges(text, 0);
    return render(text, merge([...keys, ...ranges]));
};
