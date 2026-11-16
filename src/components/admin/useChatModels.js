import { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

/**
 * The chat models this deployment offers ([{id, name, local}]), read once: the editor's "Written by" and
 * "Answered by" pickers (character profiles spec). `local` false means a cloud model, so what is sent leaves the
 * machine. An empty list on any failure.
 */
export function useChatModels(apiHost, apiPort) {
  const [models, setModels] = useState([]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/inference/models');
        if (!res?.ok) return;
        const body = await res.json();
        if (!cancelled && Array.isArray(body?.models)) {
          setModels(body.models.map((m) => ({ id: m.id, name: m.name || m.id, local: m.local !== false })));
        }
      } catch { /* no models: the pickers say so */ }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort]);
  return models;
}

/** A model's label in a picker: a cloud one says that what is sent leaves the machine. */
export const modelLabel = (m) => (m.local ? m.name : `${m.name} (cloud: your notes leave this machine)`);
