/**
 * One place that turns a refused API call into a notice a person can act on
 * (A1 spec §8; A0 adds its codes here). Server refusals arrive as
 * { detail: { error, message, ...extra } }; older routes send a string detail.
 * Only a 5xx blames the server — a 404/409 is a permission or state problem,
 * and saying "server error" there sends people looking for the wrong fix.
 */
const CONTENT_SHARED = {
    other_holders: "Other people also use this document, so it can't be changed here. Ask an admin.",
    in_project: 'This document is in a project, so changing it would change it for the project too. Remove it from the project first, or ask an admin.',
};

export function noticeFor(status, detail) {
    const d = detail && typeof detail === 'object' ? detail : null;
    if (status === 409 && d?.error === 'content_shared') {
        return CONTENT_SHARED[d.reason] || CONTENT_SHARED.other_holders;
    }
    if (status === 413) return `File too large (limit ${d?.limit_mb ?? '?'} MB).`;
    if (status === 415) return 'Unsupported file type.';
    if (status === 422 && d?.error === 'empty_file') return 'The file is empty.';
    if (status === 404) return "This document doesn't exist or you don't have access.";
    if (status >= 500) return 'Something went wrong on the server. Try again.';
    if (d?.message) return d.message;
    if (typeof detail === 'string' && detail) return detail;
    return `Request failed (HTTP ${status}).`;
}

export async function describeRefusal(res) {
    let detail = null;
    try {
        detail = (await res.json())?.detail ?? null;
    } catch {
        // non-JSON error body (proxy page, empty) — fall back to the status
    }
    return noticeFor(res.status, detail);
}
