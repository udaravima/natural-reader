/**
 * One function per route of the phone lines admin API (/v1/admin/phone/*). Each resolves to parsed JSON, or
 * throws an Error whose message is the notice to show, with its HTTP status and `reason` code on it.
 *
 * The shared noticeFor turns every 5xx into "Something went wrong on the server", but a save's 502 voice_failed
 * carries the voice engine's own reason, and without it an admin can't tell why the voice failed (R12.2). So a
 * phone refusal's own `message` is shown whatever the status; noticeFor only answers when there is none.
 */
import { apiFetch } from '../utils/apiFetch';
import { noticeFor } from './apiErrors';

const BASE = '/v1/admin/phone';
const enc = encodeURIComponent;

// What the console calls each body field a 422 can name.
const FIELD_NAMES = {
  name: 'Name', model: 'Model', voice: 'Voice', greeting: 'Welcome message', maxCallS: 'Longest call',
  maxCalls: 'Calls at once', dailyTokens: 'Tokens a day', profileId: 'Answers as', projectId: 'Knows', enabled: 'Line is on',
};

/** A Pydantic 422 is a list of {loc, msg}; name the first field, which the generic notice can't. */
function fieldProblem(detail) {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const { loc = [], msg } = detail[0] ?? {};
  const field = loc.find((part) => typeof part === 'string' && part !== 'body');
  // The form shows the longest call in minutes, so a message quoting seconds would name a number it doesn't have.
  if (field === 'maxCallS') return 'Longest call: must be 1 to 60 minutes.';
  return `${FIELD_NAMES[field] ?? field ?? 'A field'}: ${msg ?? 'is not valid'}.`;
}

async function refusal(res) {
  let detail = null;
  try {
    detail = (await res.json())?.detail ?? null;
  } catch {
    // not JSON (a proxy page, an empty body): fall back to the status
  }
  const message = fieldProblem(detail)
    ?? (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message
      ? detail.message
      : noticeFor(res.status, detail, { serverNotFound: true }));
  return Object.assign(new Error(message), { status: res.status, reason: detail?.reason ?? detail?.error });
}

export function phoneApi(host, port) {
  const call = async (path, { method = 'GET', body } = {}) => {
    const opts = { method };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const res = await apiFetch(host, port, `${BASE}${path}`, opts);
    if (!res.ok) throw await refusal(res);
    return res.json();
  };
  const line = (id) => `/lines/${enc(id)}`;
  return {
    status: () => call('/status'),
    voices: () => call('/voices'),
    lines: () => call('/lines'),
    getLine: (id) => call(line(id)),
    createLine: (body) => call('/lines', { method: 'POST', body }),
    updateLine: (id, body) => call(line(id), { method: 'PATCH', body }),
    restoreAccess: (id) => call(`${line(id)}/access`, { method: 'POST' }),
    deleteLine: (id) => call(line(id), { method: 'DELETE' }),
    calls: (id) => call(`${line(id)}/calls`),
    call: (id) => call(`/calls/${enc(id)}`),
    deleteCall: (id) => call(`/calls/${enc(id)}`, { method: 'DELETE' }),
    /** The saved welcome as a WAV Blob, for "Hear the welcome". */
    greeting: async (id) => {
      const res = await apiFetch(host, port, `${BASE}${line(id)}/clips/greeting`);
      if (!res.ok) throw await refusal(res);
      return res.blob();
    },
  };
}
