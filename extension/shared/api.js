// TTS backend client. fetchImpl is injectable for tests; defaults to global fetch.

function base64ToBlob(b64, type = 'audio/wav') {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type });
}

export async function health({ baseUrl, fetchImpl = globalThis.fetch } = {}) {
    try {
        const res = await fetchImpl(`${baseUrl}/v1/health`);
        if (!res.ok) return false;
        const data = await res.json();
        return data.status === 'ok' && data.model_loaded === true;
    } catch {
        return false;
    }
}

export async function synthesize({
    sentences, voice, speed, baseUrl, fetchImpl = globalThis.fetch, signal,
} = {}) {
    const res = await fetchImpl(`${baseUrl}/v1/batch_synthesize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sentences, voice, speed }),
        signal,
    });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const err = await res.json();
            if (err && err.detail) detail = err.detail;
        } catch { /* body not JSON — keep status */ }
        throw new Error(`TTS request failed: ${detail}`);
    }
    const data = await res.json();
    return base64ToBlob(data.audio_base64);
}
