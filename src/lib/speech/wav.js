const HEADER_BYTES = 44;

/** 16-bit mono PCM, the one format that needs no encoder in the browser and no guessing on the server. */
export function encodeWav(samples, sampleRate) {
  const dataBytes = samples.length * 2;
  const view = new DataView(new ArrayBuffer(HEADER_BYTES + dataBytes));
  const text = (at, value) => { for (let i = 0; i < value.length; i += 1) view.setUint8(at + i, value.charCodeAt(i)); };
  text(0, 'RIFF');
  view.setUint32(4, 36 + dataBytes, true);
  text(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);              // size of this block
  view.setUint16(20, 1, true);               // PCM
  view.setUint16(22, 1, true);               // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);  // bytes per second
  view.setUint16(32, 2, true);               // bytes per sample
  view.setUint16(34, 16, true);              // bits per sample
  text(36, 'data');
  view.setUint32(40, dataBytes, true);
  samples.forEach((sample, i) => {
    const clipped = Math.max(-1, Math.min(1, sample));
    view.setInt16(HEADER_BYTES + i * 2, clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff, true);
  });
  return new Blob([view], { type: 'audio/wav' });
}
