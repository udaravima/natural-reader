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

const maskToken = (n) => `<masked, ${n} chars>`;
const maskUrlToken = (n) => `<masked URL, ${n} chars>`;
// A NUL is maskEnv's internal mark for a private-key block (see there).
const NUL = String.fromCharCode(0);
const PLACEHOLDER_SHAPE = '<masked(?: URL)?, \\d+ chars>';
const HOLDS_MASK_RE = new RegExp(`${PLACEHOLDER_SHAPE}|${NUL}`);
const IS_MASK_RE = new RegExp(`^${PLACEHOLDER_SHAPE}$`);
const hasMask = (s) => HOLDS_MASK_RE.test(s);
const isAlreadyMasked = (s) => IS_MASK_RE.test(s);

// An unterminated block (a partial paste) masks to the end of the text.
const PRIVATE_KEY_RE = /-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|$)/g;
// (?<!...) anchors keep these linear on long unbroken runs.
const URL_PASSWORD_RE = /(?<![a-z0-9+.-])([a-z][a-z0-9+.-]*:\/\/[^\s:/@]*:)([^\s/]+)(?=@)/gi;
const KEYWORD_RE = /password|passwd|secret|token|api[_-]?key|private[_-]?key/gi;
const OPERATOR_RE = /===|!==|==|!=|:=|=>|=|:(?!:)/y;
const USERINFO_RE = /[^\s@/]+@/y;
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

// Where a quoted value ends: the matching quote on the same line, with `\x`
// and a doubled quote staying inside. Returns -1 if it never closes.
const closingQuote = (text, open) => {
    const q = text[open];
    for (let j = open + 1; j < text.length; j += 1) {
        const c = text[j];
        if (c === '\n' || c === '\r') return -1;
        if (c === '\\') j += 1;
        else if (c === q) {
            if (text[j + 1] !== q) return j;
            j += 1;
        }
    }
    return -1;
};

// Index of the bracket that closes the one at `open`, stepping over quoted
// strings and counting nested brackets; -1 if it does not close on this line.
const matchingBracket = (text, open) => {
    let depth = 0;
    for (let j = open; j < text.length; j += 1) {
        const c = text[j];
        if (c === '\n' || c === '\r') return -1;
        if (c === '"' || c === "'") {
            const q = closingQuote(text, j);
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

const lineEnd = (text, from) => {
    let j = from;
    while (j < text.length && text[j] !== '\n' && text[j] !== '\r') j += 1;
    return j;
};

// Finds `key <op> value` where the key holds a secret-ish word and masks the
// value. Scanning resumes at the end of each value (never the end of the line),
// so later secrets on the same line are still found.
const maskKeyValues = (text) => {
    let out = '';
    let copied = 0;
    let count = 0;
    let floor = 0;
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

        // `https://token:pw@host` is the URL rule's job: decide before walking the value.
        if (text.slice(keyStart - 3, keyStart) === '://' && text[opEnd] !== '"' && text[opEnd] !== "'") {
            USERINFO_RE.lastIndex = opEnd;
            if (USERINFO_RE.test(text)) { resume(opEnd); continue; }
        }

        let start;
        let end;
        let quote = '';
        const close = (text[p] === '"' || text[p] === "'") ? closingQuote(text, p) : -1;
        if (close >= 0) {
            quote = text[p];
            start = p + 1;
            end = close;
        } else {
            let walk = keyStart;
            while (walk > floor && PREFIX_CHAR_RE.test(text[walk - 1])) walk -= 1;
            const lineOnly = walk === 0 || text[walk - 1] === '\n' || text[walk - 1] === '\r';
            start = p;
            if (lineOnly || text[p] === '"' || text[p] === "'") {
                end = lineEnd(text, p);
                while (end > start && /[ \t]/.test(text[end - 1])) end -= 1;
            } else if (text[p] === '[' || text[p] === '{') {
                // a bracketed value runs to its match on this line, else to the line's end
                const closeAt = matchingBracket(text, p);
                end = closeAt >= 0 ? closeAt + 1 : lineEnd(text, p);
            } else if (quotedKey) {
                end = p;
                while (end < text.length && !/[\s,}\]]/.test(text[end])) end += 1;
            } else {
                end = p;
                while (end < text.length && !/\s/.test(text[end])) end += 1;
                while (end > start && /[,;)}\]]/.test(text[end - 1])) end -= 1;
            }
        }
        const value = text.slice(start, end);
        const after = quote ? end + 1 : end;
        resume(Math.max(after, opEnd));
        if (!value || hasMask(value) || PLAIN_WORDS_RE.test(value)) continue;
        out += text.slice(copied, start) + maskToken(value.length);
        copied = end;
        count += 1;
    }
    return { text: out + text.slice(copied), masked: count };
};

// Each pass leaves only "<masked ...>" placeholders behind. The key rule and
// the URL rule skip values that already hold one; the token and JWT patterns
// cannot match inside one. So a value counts once.
const maskGeneric = (text) => {
    let count = 0;
    const hit = (n) => { count += 1; return maskToken(n); };
    let out = text.replace(PRIVATE_KEY_RE, (m) => hit(m.length));
    const kv = maskKeyValues(out);
    out = kv.text;
    count += kv.masked;
    out = out.replace(URL_PASSWORD_RE, (m, head, pw) => head + hit(pw.length));
    out = out.replace(TOKEN_RE, (m) => hit(m.length));
    out = out.replace(JWT_RE, (m) => hit(m.length));
    return { text: out, masked: count };
};

const MARK_RE = new RegExp(`${NUL}(\\d+)${NUL}`, 'g');
const ENV_HEAD_RE = /^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*[ \t]*=/;
// Content detection is strict: `key = "x"` in source code is not an env file.
const ENV_DETECT_RE = /^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*=/;
const ENV_DETECT_SPACED_RE = /^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*\s*=/;
const SPACED_CONFIG_EXT = /\.(ini|toml|cfg|conf|properties)$/;

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

// failClosed (env by NAME): a line that does not parse as KEY=value is masked
// after its first = or :, or whole; a quote that never closes runs to the end.
const maskEnv = (text, failClosed) => {
    let count = 0;
    // A private-key block becomes an internal mark: NUL, its length, NUL. It is
    // counted once, later. readTextFile rejects any file holding a NUL, so a
    // mark can never be mistaken for text that was in the file.
    const pre = text.replace(PRIVATE_KEY_RE, (m) => `${NUL}${m.length}${NUL}`);
    const lines = pre.split('\n');
    const out = [];
    for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        let cr = line.endsWith('\r') ? '\r' : '';
        const body = cr ? line.slice(0, -1) : line;
        if (body.trim() === '') { out.push(line); continue; }
        // a control character (but tab) or a line separator inside a comment means it is not one line
        if (body.trimStart().startsWith('#') && !/[\p{Zl}\p{Zp}]|[^\P{Cc}\t]/u.test(body.trim())) {
            const g = maskGeneric(body);
            count += g.masked;
            out.push(g.text + cr);
            continue;
        }
        let head;
        let rest;
        const hm = ENV_HEAD_RE.exec(body);
        if (hm) {
            head = hm[0];
            rest = body.slice(head.length);
        } else if (failClosed) {
            const sep = body.search(/[=:]/);
            const cut = sep >= 0 ? sep + 1 : body.length - body.trimStart().length;
            head = body.slice(0, cut);
            rest = body.slice(cut);
        } else {
            const g = maskGeneric(body);
            count += g.masked;
            out.push(g.text + cr);
            continue;
        }
        const trimmedStart = rest.trimStart();
        const gap = rest.slice(0, rest.length - trimmedStart.length);
        const v = trimmedStart.trimEnd();
        let tail = trimmedStart.slice(v.length);
        let inner = v;
        let multiline = false;
        const q = v[0];
        if (q === '"' || q === "'") {
            if (v.length >= 2 && v.endsWith(q)) {
                inner = v.slice(1, -1);
            } else if (v.indexOf(q, 1) < 0) {
                // opens a quote that does not close on this line: one value
                multiline = true;
                inner = v.slice(1) + cr;
                cr = '';
                tail = '';
                while (i + 1 < lines.length && !(i + 1 === lines.length - 1 && lines[i + 1] === '')) {
                    i += 1;
                    const l = lines[i];
                    const lcr = l.endsWith('\r') ? '\r' : '';
                    if (l.includes(q)) {
                        inner += `\n${lcr ? l.slice(0, -1) : l}`;
                        cr = lcr;
                        if (inner.endsWith(q)) inner = inner.slice(0, -1);
                        break;
                    }
                    inner += `\n${l}`;
                }
            }
        }
        if (!multiline && (inner === '' || isAlreadyMasked(inner) || ENV_PLAIN_RE.test(inner)
            || /^\d{1,6}$/.test(inner))) {
            out.push(line);
            continue;
        }
        // A value holding marks is one masked value, at the length of what was there.
        const realLength = inner.replace(MARK_RE, (mk, n) => ' '.repeat(Number(n))).length;
        count += 1;
        const token = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(inner)
            ? maskUrlToken(realLength) : maskToken(realLength);
        out.push(head + gap + token + tail + cr);
    }
    // Marks that no value took with it (a comment line, a key) are masked here.
    const joined = out.join('\n').replace(MARK_RE, (mk, n) => { count += 1; return maskToken(Number(n)); });
    return { text: joined, masked: count };
};

export const maskText = (name, text) => {
    if (isEnvName(name)) return maskEnv(text, true);
    if (looksLikeEnv(name, text)) return maskEnv(text, false);
    return maskGeneric(text);
};
