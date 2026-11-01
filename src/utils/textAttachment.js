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
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (bytes.length > TEXT_FILE_MAX_BYTES) {
        return { ok: false, error: `${file.name} is over 64 KB` };
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
const isAlreadyMasked = (s) => /^<masked[^>]*>$/.test(s);

const PRIVATE_KEY_RE = /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/g;
const URL_PASSWORD_RE = /(\b[a-z][a-z0-9+.-]*:\/\/[^\s:/@]+:)([^\s/]+)(?=@)/gi;
const SECRET_KEY_RE = new RegExp(
    '([\\w.-]*(?:password|passwd|secret|token|api[_-]?key|apikey|private[_-]?key)[\\w.-]*["\']?[ \\t]*[:=][ \\t]*)'
    + '(?:(["\'])([^\\n]*?)\\2|([^\\s,;]+))',
    'gi',
);
const TOKEN_RE = new RegExp(
    '\\b(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{8,}'
    + '|github_pat_[A-Za-z0-9_]{8,}|xox[abprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|nrp_[A-Za-z0-9_-]{8,})',
    'g',
);
const JWT_RE = /\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g;
const ENV_PLAIN_RE = /^(true|false|yes|no|on|off)$/i;
const PLAIN_WORDS_RE = /^(true|false|yes|no|on|off|null|none)$/i;

const maskGeneric = (text) => {
    let count = 0;
    const hit = (n) => { count += 1; return maskToken(n); };
    let out = text.replace(PRIVATE_KEY_RE, (m) => hit(m.length));
    out = out.replace(URL_PASSWORD_RE, (m, head, pw) =>
        (isAlreadyMasked(pw) ? m : head + hit(pw.length)));
    out = out.replace(SECRET_KEY_RE, (m, head, quote, inner, bare) => {
        const value = quote ? inner : bare;
        if (!value || isAlreadyMasked(value) || PLAIN_WORDS_RE.test(value)) return m;
        const masked = hit(value.length);
        return quote ? head + quote + masked + quote : head + masked;
    });
    out = out.replace(TOKEN_RE, (m) => hit(m.length));
    out = out.replace(JWT_RE, (m) => hit(m.length));
    return { text: out, masked: count };
};

const ENV_LINE_RE = /^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*\s*=)(\s*)(.*?)(\s*)$/;
// Detection is stricter than the line rule: `key = "x"` in source code is not an env file.
const ENV_DETECT_RE = /^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*=/;

const isEnvName = (name) => {
    const base = (name || '').toLowerCase().split(/[\\/]/).pop();
    return base === '.env' || base.startsWith('.env.') || base.endsWith('.env');
};

const looksLikeEnv = (text) => {
    const lines = text.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
    if (!lines.length) return false;
    return lines.filter((l) => ENV_DETECT_RE.test(l)).length / lines.length >= 0.6;
};

const maskEnv = (text) => {
    // A private key block may span lines; mask it first, and let the line pass skip it.
    let count = 0;
    const pre = text.replace(PRIVATE_KEY_RE, (m) => { count += 1; return maskToken(m.length); });
    const lines = pre.split('\n').map((line) => {
        const cr = line.endsWith('\r') ? '\r' : '';
        const body = cr ? line.slice(0, -1) : line;
        if (body.trimStart().startsWith('#')) return line;
        const m = ENV_LINE_RE.exec(body);
        if (!m) {
            const g = maskGeneric(body);
            count += g.masked;
            return g.text + cr;
        }
        const [, head, gap, value, tail] = m;
        const quoted = value.length >= 2 && /^(["']).*\1$/.test(value);
        const inner = quoted ? value.slice(1, -1) : value;
        if (inner === '' || isAlreadyMasked(inner) || ENV_PLAIN_RE.test(inner)
            || /^\d{1,6}$/.test(inner)) return line;
        count += 1;
        const token = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(inner)
            ? maskUrlToken(inner.length) : maskToken(inner.length);
        return head + gap + token + tail + cr;
    });
    return { text: lines.join('\n'), masked: count };
};

export const maskText = (name, text) =>
    (isEnvName(name) || looksLikeEnv(text)) ? maskEnv(text) : maskGeneric(text);
