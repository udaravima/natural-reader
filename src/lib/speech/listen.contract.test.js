/**
 * What listen.js relies on inside vad-web, checked against the real library rather than the mock listen.test.js uses.
 * vad-web has no public hook for a per-frame score, so the voice gate replaces the function its FrameProcessor asks.
 * If an upgrade moves that, these fail, instead of Talk silently letting every voice through again.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { FrameProcessor } from '@ricky0123/vad-web/dist/frame-processor';
import { Message } from '@ricky0123/vad-web/dist/messages';

const OPTIONS = {
  positiveSpeechThreshold: 0.5, negativeSpeechThreshold: 0.35, redemptionMs: 700, preSpeechPadMs: 300,
  minSpeechMs: 250, submitUserSpeechOnPause: false,
};
const FRAME = new Float32Array(512);

async function feed(processor, count) {
  const events = [];
  for (let i = 0; i < count; i += 1) await processor.process(FRAME, (ev) => events.push(ev.msg));
  return events;
}

function processor() {
  const p = new FrameProcessor(async () => { throw new Error('the replaced function was called'); }, () => {}, OPTIONS, 32);
  p.resume();
  return p;
}

describe('vad-web, as listen.js uses it', () => {
  it('asks modelProcessFunc for every frame at the time, so a replacement decides', async () => {
    const p = processor();
    let score = 0.9;
    p.modelProcessFunc = async () => ({ isSpeech: score, notSpeech: 1 - score });
    expect(await feed(p, 8)).toContain(Message.SpeechRealStart);
    score = 0;
    expect(await feed(p, 22)).toContain(Message.SpeechEnd);
  });

  it('setOptions shortens the silence that ends a turn, from the next frame (Send)', async () => {
    const p = processor();
    p.modelProcessFunc = async () => ({ isSpeech: 0.9, notSpeech: 0.1 });
    await feed(p, 10);
    p.setOptions({ redemptionMs: 32 });
    p.modelProcessFunc = async () => ({ isSpeech: 0, notSpeech: 1 });
    expect(await feed(p, 1)).toContain(Message.SpeechEnd);
  });

  it('MicVAD keeps its FrameProcessor as `frameProcessor`, and builds it once', () => {
    const require = createRequire(import.meta.url);
    const source = readFileSync(require.resolve('@ricky0123/vad-web/dist/real-time-vad.js'), 'utf8');
    expect(source).toMatch(/this\.frameProcessor = frameProcessor;/);
    expect(source.match(/new frame_processor_1\.FrameProcessor\(/g)).toHaveLength(1);
  });
});
