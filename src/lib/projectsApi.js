/**
 * One function per A0 call: projects, members, activity, the people lookup,
 * per-document shares, the admin project list, and a project's documents and
 * their versions (V1). Each resolves to parsed
 * JSON (null for a 204) or throws an Error whose message is the notice to
 * show (apiErrors.js) — never a raw "HTTP nnn".
 */
import { apiFetch, apiUpload } from '../utils/apiFetch';
import { describeRefusal, noticeFor, PROJECT_NOT_FOUND } from './apiErrors';

const enc = encodeURIComponent;

export function projectsApi(host, port) {
  const notice = { serverNotFound: true, notFound: PROJECT_NOT_FOUND };
  const call = async (path, { method = 'GET', body } = {}) => {
    const opts = { method };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const res = await apiFetch(host, port, path, opts);
    if (!res.ok) throw new Error(await describeRefusal(res, notice));
    return res.status === 204 ? null : res.json();
  };
  const project = (id) => `/v1/projects/${enc(id)}`;
  // Multipart: no Content-Type header, so the browser sets the boundary.
  const upload = async (path, file, note) => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (note) form.append('note', note);
    const res = await apiFetch(host, port, path, { method: 'POST', body: form });
    if (!res.ok) throw new Error(await describeRefusal(res, notice));
    return res.json();
  };
  // Filing (V1 spec §4.2): a name or bytes the project already has is a
  // question for the person, not an error — returned as { conflict }.
  const FILING_CONFLICTS = new Set(['name_exists', 'older_version']);
  const fileInto = async (projectId, contentId, choice) => {
    const query = choice?.asVersionOf ? `?as_version_of=${enc(choice.asVersionOf)}`
      : choice?.keepBoth ? '?keep_both=true' : '';
    const res = await apiFetch(host, port, `${project(projectId)}/docs/${enc(contentId)}${query}`, { method: 'PUT' });
    if (!res.ok) {
      let detail = null;
      try {
        detail = (await res.json())?.detail ?? null;
      } catch {
        // non-JSON body — fall back to the status
      }
      if (res.status === 409 && FILING_CONFLICTS.has(detail?.error)) return { conflict: detail };
      throw new Error(noticeFor(res.status, detail, notice));
    }
    return { filed: res.status === 204 ? null : await res.json() };
  };
  // Bulk upload: one file stored and filed (the server decides again from the project as it is). A refusal
  // throws an Error carrying the server's sentence, its status and its code, so the batch can tell "you lost
  // access" from "this file was refused".
  const uploadFile = async (projectId, file, fields, opts) => {
    const form = new FormData();
    form.append('file', file, file.name);
    for (const key of ['on_match', 'on_older', 'document_id']) {
      if (fields?.[key]) form.append(key, fields[key]);
    }
    const res = await apiUpload(host, port, `${project(projectId)}/uploads`, form, opts);
    if (res.ok) return res.body;
    const detail = res.body?.detail ?? null;
    const err = new Error(res.status === 0 ? 'The upload was cut off. Check the connection and try again.'
      : noticeFor(res.status, detail, notice));
    err.status = res.status;
    err.code = detail && typeof detail === 'object' ? detail.error : undefined;
    throw err;
  };
  const documents = (id) => `${project(id)}/documents`;
  const document = (id, docId) => `${documents(id)}/${enc(docId)}`;
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
    fileDoc: (id, contentId, choice) => fileInto(id, contentId, choice),
    documents: (id) => call(documents(id)),
    versions: (id, docId) => call(`${document(id, docId)}/versions`),
    uploadVersion: (id, docId, file, note) => upload(`${document(id, docId)}/versions`, file, note),
    versionFromLibrary: (id, docId, contentId, note) => call(`${document(id, docId)}/versions/from-library`,
      { method: 'POST', body: { doc_id: contentId, note: note || null } }),
    restoreVersion: (id, docId, n) => call(`${document(id, docId)}/versions/${n}/restore`, { method: 'POST' }),
    deleteVersion: (id, docId, n) => call(`${document(id, docId)}/versions/${n}`, { method: 'DELETE' }),
    mergeDocuments: (id, intoId, fromId) => call(`${document(id, intoId)}/merge`, { method: 'POST', body: { from: fromId } }),
    removeDocument: (id, docId) => call(document(id, docId), { method: 'DELETE' }),
    uploadPlan: (id, files) => call(`${project(id)}/uploads/plan`, { method: 'POST', body: { files } }),
    uploadFile,
    myDocs: () => call('/v1/docs'),
    reindex: (docId) => call(`/v1/docs/${enc(docId)}/index`, { method: 'POST' }),
    lookup: (q) => call(`/v1/users/lookup?q=${enc(q)}`),
    shares: (docId) => call(`/v1/docs/${enc(docId)}/shares`),
    share: (docId, userId) => call(`/v1/docs/${enc(docId)}/shares/${enc(userId)}`, { method: 'PUT' }),
    unshare: (docId, userId) => call(`/v1/docs/${enc(docId)}/shares/${enc(userId)}`, { method: 'DELETE' }),
    adminProjects: (ownerless) => call(`/v1/admin/projects${ownerless ? '?ownerless=true' : ''}`),
  };
}
