/**
 * Concatenate multiple WAV blobs of identical PCM format into a single WAV.
 *
 * Kokoro returns RIFF/WAVE PCM with fixed sample rate / channels / bit depth
 * per server config, so we trust that every input shares format. The first
 * blob's header is reused as the template; all subsequent blobs contribute
 * only their `data` chunk payload. The output's RIFF and `data` sizes are
 * rewritten to match the concatenated payload length.
 *
 * If the inputs differ in format (sample rate, channels, etc.) the result
 * may still play but will be wrong — there's nothing client-side we can do
 * to resample without pulling in a heavy dep, so the caller must guarantee
 * format consistency (i.e. always use the same voice and speed).
 */
export async function concatWavs(blobs) {
    if (!blobs || blobs.length === 0) return null;
    if (blobs.length === 1) return blobs[0];

    const buffers = await Promise.all(blobs.map((b) => b.arrayBuffer()));

    // First file: keep its full header (everything up to the data payload).
    const firstView = new DataView(buffers[0]);
    const firstDataOffset = findDataChunkOffset(firstView);
    if (firstDataOffset < 0) {
        throw new Error('First WAV has no data chunk');
    }
    const headerEnd = firstDataOffset; // first payload byte offset
    const header = new Uint8Array(buffers[0], 0, headerEnd);

    // Collect each file's data payload.
    const payloads = [];
    let totalPayload = 0;
    for (const buf of buffers) {
        const view = new DataView(buf);
        const dataOffset = findDataChunkOffset(view);
        if (dataOffset < 0) continue;
        const dataSize = view.getUint32(dataOffset - 4, true);
        const payload = new Uint8Array(buf, dataOffset, dataSize);
        payloads.push(payload);
        totalPayload += dataSize;
    }

    const finalSize = header.length + totalPayload;
    const out = new Uint8Array(finalSize);
    out.set(header, 0);
    let offset = header.length;
    for (const p of payloads) {
        out.set(p, offset);
        offset += p.length;
    }

    // Rewrite RIFF chunk size (file size - 8) and data chunk size.
    const outView = new DataView(out.buffer);
    outView.setUint32(4, finalSize - 8, true);
    outView.setUint32(headerEnd - 4, totalPayload, true);

    return new Blob([out], { type: 'audio/wav' });
}

// Walk top-level RIFF sub-chunks (starting at offset 12 — after "RIFF<size>WAVE")
// until we hit the `data` chunk. Returns the offset of the first payload byte,
// or -1 if no data chunk was found.
function findDataChunkOffset(view) {
    let offset = 12;
    while (offset + 8 <= view.byteLength) {
        const tag = String.fromCharCode(
            view.getUint8(offset),
            view.getUint8(offset + 1),
            view.getUint8(offset + 2),
            view.getUint8(offset + 3),
        );
        const chunkSize = view.getUint32(offset + 4, true);
        if (tag === 'data') return offset + 8;
        offset += 8 + chunkSize;
        // RIFF chunks are word-aligned — odd sizes get a pad byte.
        if (chunkSize % 2 === 1) offset += 1;
    }
    return -1;
}
