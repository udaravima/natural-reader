// Resolves Markdown link hrefs to workspace-relative paths (or classifies them
// as external / anchor). Pure: no I/O, fully unit-tested.

const EXTERNAL_RE = /^([a-z][a-z0-9+.-]*:|\/\/)/i; // http:, https:, mailto:, //cdn

export function isExternal(href) {
    return EXTERNAL_RE.test(href || '');
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
