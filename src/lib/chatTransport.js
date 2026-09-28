// Server-side chat helpers (C1: the browser no longer talks to Ollama, and
// Local mode is gone). The turn itself is src/lib/chatStream.js.
export const MODELS_PATH = '/v1/inference/models';

// 429 from the server means the daily token budget is gone.
export async function budgetDetail(res) {
  if (res.status !== 429) return null;
  try {
    const body = await res.json();
    const detail = body?.detail;
    return detail && typeof detail === 'object' ? detail : null;
  } catch {
    return null;
  }
}

export function formatResetAt(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
