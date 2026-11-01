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
const hasMask = (s) => s.includes('<masked');
const isAlreadyMasked = (s) => /^<masked[^>]*>$/.test(s);

// An unterminated block (a partial paste) masks to the end of the text.
const PRIVATE_KEY_RE = /-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|$)/g;
// (?<!...) anchors keep these linear on long unbroken runs.
const URL_PASSWORD_RE = /(?<![a-z0-9+.-])([a-z][a-z0-9+.-]*:\/\/[^\s:/@]*:)([^\s/]+)(?=@)/gi;
const SECRET_KEY_RE = /(?<![\w.-])([\w.-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)[\w.-]*["']?[ \t]*(?:=>|[:=])[ \t]*)(?:(["'])((?:\\[\s\S]|(?!\2)[^\\\r\n])*)\2|([^\r\n]*))/gi;
const TOKEN_RE = new RegExp(
    '\\b(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}'
    + '|github_pat_[A-Za-z0-9_]{8,}|xox[abprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|nrp_[A-Za-z0-9_-]{8,})',
    'g',
);
const JWT_RE = /\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g;
const ENV_PLAIN_RE = /^(true|false|yes|no|on|off)$/i;
const PLAIN_WORDS_RE = /^(true|false|yes|no|on|off|null|none)$/i;

// Each pass leaves only "<masked ...>" placeholders behind, and no later pass
// re-masks one (hasMask guards), so a value counts once.
const maskGeneric = (text) => {
    let count = 0;
    const hit = (n) => { count += 1; return maskToken(n); };
    let out = text.replace(PRIVATE_KEY_RE, (m) => hit(m.length));
    out = out.replace(SECRET_KEY_RE, (m, head, quote, inner, bare, offset, whole) => {
        let value = quote ? inner : bare;
        let rest = '';
        if (!quote) {
            const lineStart = whole.lastIndexOf('\n', offset - 1) + 1;
            const prefix = whole.slice(lineStart, offset);
            if (/^[ \t#/*>-]*$/.test(prefix)) {
                // a line that is only `key: value`: the rest of the line is the value
                const t = value.trimEnd();
                rest = value.slice(t.length);
                value = t;
            } else {
                const sp = value.search(/\s/);
                if (sp >= 0) { rest = value.slice(sp); value = value.slice(0, sp); }
                // `https://x-access-token:pw@host` is the URL rule's job
                if (prefix.endsWith('//') && /^[^\s@/]+@/.test(value)) return m;
            }
        }
        if (!value || hasMask(value) || PLAIN_WORDS_RE.test(value)) return m;
        const masked = hit(value.length);
        return quote ? head + quote + masked + quote : head + masked + rest;
    });
    out = out.replace(URL_PASSWORD_RE, (m, head, pw) => (hasMask(pw) ? m : head + hit(pw.length)));
    out = out.replace(TOKEN_RE, (m) => hit(m.length));
    out = out.replace(JWT_RE, (m) => hit(m.length));
    return { text: out, masked: count };
};

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
    const pre = text.replace(PRIVATE_KEY_RE, (m) => { count += 1; return maskToken(m.length); });
    const lines = pre.split('\n');
    const out = [];
    for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        let cr = line.endsWith('\r') ? '\r' : '';
        const body = cr ? line.slice(0, -1) : line;
        if (body.trim() === '') { out.push(line); continue; }
        if (body.trimStart().startsWith('#')) {
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
                inner = v.slice(1);
                tail = '';
                while (i + 1 < lines.length && !(i + 1 === lines.length - 1 && lines[i + 1] === '')) {
                    i += 1;
                    const l = lines[i];
                    cr = l.endsWith('\r') ? '\r' : '';
                    inner += `\n${cr ? l.slice(0, -1) : l}`;
                    if (l.includes(q)) {
                        if (inner.endsWith(q)) inner = inner.slice(0, -1);
                        break;
                    }
                }
            }
        }
        if (!multiline && (inner === '' || isAlreadyMasked(inner) || ENV_PLAIN_RE.test(inner)
            || /^\d{1,6}$/.test(inner))) {
            out.push(line);
            continue;
        }
        count += 1;
        const token = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(inner)
            ? maskUrlToken(inner.length) : maskToken(inner.length);
        out.push(head + gap + token + tail + cr);
    }
    return { text: out.join('\n'), masked: count };
};

export const maskText = (name, text) => {
    if (isEnvName(name)) return maskEnv(text, true);
    if (looksLikeEnv(name, text)) return maskEnv(text, false);
    return maskGeneric(text);
};
