// Resolves Markdown link hrefs to workspace-relative paths (or classifies them
// as external / anchor / unsafe). Pure: no I/O, fully unit-tested.

// Only these schemes are safe to emit as a link href. Anything else with an
// explicit scheme (javascript:, data:, vbscript:, file:, …) is rejected by
// resolvePath as { kind: 'unsafe' } so the renderer never produces such an href.
const SAFE_EXTERNAL_RE = /^(https?:|mailto:|\/\/)/i; // http(s):, mailto:, //cdn
const SCHEME_RE = /^[a-z][a-z0-9+.-]*:/i;            // any explicit URI scheme
// ASCII control chars (built via RegExp ctor to avoid embedding literal ones).
const CONTROL_CHARS = new RegExp('[\\u0000-\\u001F]', 'g');

export function isExternal(href) {
    return SAFE_EXTERNAL_RE.test(href || '');
}

export function dirname(relPath) {
    const i = (relPath || '').lastIndexOf('/');
    return i === -1 ? '' : relPath.slice(0, i);
}

// Collapse '.' and '..' segments in a posix-style path.
function normalize(path) {
    const out = [];
    for (const seg of path.split('/')) {
        if (seg === '' || seg === '.') continue;
        if (seg === '..') { out.pop(); continue; }
        out.push(seg);
    }
    return out.join('/');
}

export function resolvePath(currentDir, href) {
    const raw = (href || '').trim();
    if (!raw) return { kind: 'none' };
    if (raw.startsWith('#')) return { kind: 'anchor', anchor: decodeURIComponent(raw.slice(1)) };
    if (isExternal(raw)) return { kind: 'external', href: raw };
    // Reject any other explicit scheme. Strip ASCII control chars first so
    // schemes browsers still honor with embedded tabs/newlines (java\tscript:)
    // can't slip through and be treated as a relative "path".
    const stripped = raw.replace(CONTROL_CHARS, '');
    if (SCHEME_RE.test(stripped) && !isExternal(stripped)) return { kind: 'unsafe' };

    const hashIdx = raw.indexOf('#');
    const anchor = hashIdx === -1 ? null : decodeURIComponent(raw.slice(hashIdx + 1));
    let pathPart = hashIdx === -1 ? raw : raw.slice(0, hashIdx);
    const qIdx = pathPart.indexOf('?');
    if (qIdx !== -1) pathPart = pathPart.slice(0, qIdx);
    pathPart = decodeURIComponent(pathPart);

    const base = pathPart.startsWith('/') ? '' : (currentDir || '');
    const joined = base ? `${base}/${pathPart}` : pathPart;
    return { kind: 'path', path: normalize(joined), anchor };
}
