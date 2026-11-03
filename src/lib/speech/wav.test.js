import { describe, it, expect } from 'vitest';
import { encodeWav } from './wav';

const bytes = (blob) => new Promise((resolve) => {
  const reader = new FileReader();
  reader.onload = () => resolve(new DataView(reader.result));
  reader.readAsArrayBuffer(blob);
});

describe('encodeWav', () => {
  it('writes 16-bit mono PCM at the rate it is told', async () => {
    const blob = encodeWav(new Float32Array([0, 1, -1]), 16000);
    expect(blob.type).toBe('audio/wav');
    expect(blob.size).toBe(44 + 6);
    const view = await bytes(blob);
    expect(String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3))).toBe('RIFF');
    expect(view.getUint16(22, true)).toBe(1);         // mono
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getInt16(44, true)).toBe(0);
    expect(view.getInt16(46, true)).toBe(32767);
    expect(view.getInt16(48, true)).toBe(-32768);
  });

  it('a sample past full scale stays at full scale instead of wrapping round', async () => {
    const view = await bytes(encodeWav(new Float32Array([1.5, -1.5]), 16000));
    expect(view.getInt16(44, true)).toBe(32767);
    expect(view.getInt16(46, true)).toBe(-32768);
  });
});
