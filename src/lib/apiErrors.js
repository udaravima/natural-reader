/**
 * One place that turns a refused API call into a notice a person can act on
 * (A1 spec §8, A0 spec §6). Server refusals arrive as
 * { detail: { error, message, ...extra } }; older routes send a string detail.
 * Only a 5xx blames the server — a 404/409 is a permission or state problem,
 * and saying "server error" there sends people looking for the wrong fix.
 */
const CONTENT_SHARED = {
    other_holders: "Other people also use this document, so it can't be changed here. Ask an admin.",
    in_project: 'This document is in a project, so changing it would change it for the project too. Remove it from the project first, or ask an admin.',
};

export const PROJECT_NOT_FOUND = "This project doesn't exist or you don't have access.";
// A0 §6: a member whose role is too low. `required` is the lowest role that may.
const ROLE_NOTICE = {
    contributor: 'Only Contributors, Maintainers or Owners can do that.',
    maintainer: 'Only Maintainers or Owners can do that.',
    owner: 'Only Owners can do that.',
};

export function noticeFor(status, detail, { notFound, tooLarge, serverNotFound } = {}) {
    const d = detail && typeof detail === 'object' ? detail : null;
    if (status === 409 && d?.error === 'content_shared') {
        return CONTENT_SHARED[d.reason] || CONTENT_SHARED.other_holders;
    }
    // Documents and chat both refuse with 413 too_large; the caller names what was too big.
    if (status === 413) return `${tooLarge || 'File'} too large (limit ${d?.limit_mb ?? '?'} MB).`;
    if (status === 415) return 'Unsupported file type.';
    if (status === 422 && d?.error === 'empty_file') return 'The file is empty.';
    if (status === 403 && d?.error === 'insufficient_role') {
        return ROLE_NOTICE[d.required] || d.message || "Your role in this project doesn't allow that.";
    }
    if (status === 403 && d?.error === 'project_creation_restricted') return 'Only admins can create projects here.';
    if (status === 409 && d?.error === 'last_owner') return 'A project must keep at least one Owner.';
    if (status === 429 && d?.error === 'project_limit') {
        return `You've reached your project limit (${d.limit ?? '?'}). Ask an admin to raise it.`;
    }
    // Project routes name what wasn't found ("User not found", the project
    // notice); `serverNotFound` lets their message through.
    if (status === 404) {
        return (serverNotFound && d?.message) || notFound || "This document doesn't exist or you don't have access.";
    }
    // Chat refusals (C1 spec §7.2) carry a message written for people; a 503
    // "no provider" or "database down" is a setup/outage problem, not a crash,
    // so it isn't "server error".
    if (d?.error === 'no_providers') return d.message || 'No model provider is configured.';
    if (d?.error === 'db_unavailable') return d.message || 'The database is unavailable. Try again in a moment.';
    if (status >= 500) return 'Something went wrong on the server. Try again.';
    if (d?.message) return d.message;
    if (typeof detail === 'string' && detail) return detail;
    return `Request failed (HTTP ${status}).`;
}

export async function describeRefusal(res, options = {}) {
    let detail = null;
    try {
        detail = (await res.json())?.detail ?? null;
    } catch {
        // non-JSON error body (proxy page, empty) — fall back to the status
    }
    return noticeFor(res.status, detail, options);
}
