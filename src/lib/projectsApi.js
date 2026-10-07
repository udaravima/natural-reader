/**
 * One function per A0 call: projects, members, activity, the people lookup,
 * per-document shares and the admin project list. Each resolves to parsed
 * JSON (null for a 204) or throws an Error whose message is the notice to
 * show (apiErrors.js) — never a raw "HTTP nnn".
 */
import { apiFetch } from '../utils/apiFetch';
import { describeRefusal, PROJECT_NOT_FOUND } from './apiErrors';

const enc = encodeURIComponent;

export function projectsApi(host, port) {
  const call = async (path, { method = 'GET', body } = {}) => {
    const opts = { method };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const res = await apiFetch(host, port, path, opts);
    if (!res.ok) throw new Error(await describeRefusal(res, { serverNotFound: true, notFound: PROJECT_NOT_FOUND }));
    return res.status === 204 ? null : res.json();
  };
  const project = (id) => `/v1/projects/${enc(id)}`;
  return {
    list: () => call('/v1/projects'),
    get: (id) => call(project(id)),
    create: (body) => call('/v1/projects', { method: 'POST', body }),
    update: (id, body) => call(project(id), { method: 'PATCH', body }),
    remove: (id) => call(project(id), { method: 'DELETE' }),
    members: (id) => call(`${project(id)}/members`),
    putMember: (id, userId, role) => call(`${project(id)}/members/${enc(userId)}`, { method: 'PUT', body: { role } }),
    removeMember: (id, userId) => call(`${project(id)}/members/${enc(userId)}`, { method: 'DELETE' }),
    events: (id, before) => call(`${project(id)}/events?limit=50${before ? `&before=${enc(before)}` : ''}`),
    fileDoc: (id, docId) => call(`${project(id)}/docs/${enc(docId)}`, { method: 'PUT' }),
    unfileDoc: (id, docId) => call(`${project(id)}/docs/${enc(docId)}`, { method: 'DELETE' }),
    projectDocs: (id) => call(`/v1/docs?project_id=${enc(id)}`),
    myDocs: () => call('/v1/docs'),
    lookup: (q) => call(`/v1/users/lookup?q=${enc(q)}`),
    shares: (docId) => call(`/v1/docs/${enc(docId)}/shares`),
    share: (docId, userId) => call(`/v1/docs/${enc(docId)}/shares/${enc(userId)}`, { method: 'PUT' }),
    unshare: (docId, userId) => call(`/v1/docs/${enc(docId)}/shares/${enc(userId)}`, { method: 'DELETE' }),
    adminProjects: (ownerless) => call(`/v1/admin/projects${ownerless ? '?ownerless=true' : ''}`),
  };
}
