/**
 * Document identity = sha256 hex of file bytes.
 *
 * Hashed lazily on first use (e.g. when the user clicks "Ask about this page")
 * and cached in-memory per fileName so we don't re-hash on every interaction.
 * The cache is module-scoped, lives only for the page session, and is keyed by
 * fileName — different bytes under the same name get a fresh hash because we
 * re-derive when the cached entry doesn't match the size of the new buffer.
 */
const cache = new Map(); // fileName -> { size, hash }

export async function sha256Hex(arrayBuffer) {
    const digest = await crypto.subtle.digest('SHA-256', arrayBuffer);
    return Array.from(new Uint8Array(digest))
        .map(b => b.toString(16).padStart(2, '0'))
        .join('');
}

export async function getOrComputeDocHash(fileName, arrayBuffer) {
    if (!fileName || !arrayBuffer) return null;
    const size = arrayBuffer.byteLength;
    const cached = cache.get(fileName);
    if (cached && cached.size === size) return cached.hash;
    const hash = await sha256Hex(arrayBuffer);
    cache.set(fileName, { size, hash });
    return hash;
}

export function clearDocHashCache() {
    cache.clear();
}
