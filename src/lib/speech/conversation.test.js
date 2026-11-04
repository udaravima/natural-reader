import { describe, it, expect, vi } from 'vitest';
import { createConversation } from './conversation';
import { FakeAudio } from './fakeAudio.testutil';

const SETTINGS = { max_no_speech: 0.4, min_logprob: -1.0 };
const SPEECH = { text: 'Hello there', no_speech: 0.05, avg_logprob: -0.3 };
const NOISE = { text: 'Thank you.', no_speech: 0.6, avg_logprob: -1.1 };
const tick = async () => { for (let i = 0; i < 6; i += 1) await new Promise((r) => { setTimeout(r, 0); }); };

function harness() {
  const h = { audio: new FakeAudio(), results: [], sends: [], phases: [], clips: new Map(), time: 0 };
  h.send = vi.fn((text, options) => {
    const turn = { text, options };
    turn.settled = new Promise((resolve) => { turn.settle = resolve; });
    h.sends.push(turn);
    return turn.settled;
  });
  h.abort = vi.fn(() => { h.sends.at(-1)?.settle({ sent: true }); });
  h.transcribe = vi.fn(async () => {
    const next = h.results.shift();
    if (next instanceof Error) throw next;
    return next;
  });
  h.conversation = createConversation({
    settings: SETTINGS, transcribe: h.transcribe, send: h.send, abort: h.abort, audio: h.audio,
    synthesize: async (text) => `blob:${text}`, revoke: () => {}, now: () => { h.time += 10; return h.time; },
    onChange: (phase) => h.phases.push(phase), onClip: (clip) => h.clips.set(clip.id, { ...clip }),
  });
  h.phase = () => h.phases.at(-1);
  h.speak = async (result) => {
    h.results.push(result);
    h.conversation.speechStarted();
    h.conversation.speechEnded(new Float32Array(160));
    await tick();
  };
  h.reply = async (text, id = 'a-1') => {
    const turn = h.sends.at(-1);
    turn.options.onStart(id);
    turn.options.onText(text);
    await tick();
  };
  h.finishReply = async () => { h.sends.at(-1).settle({ sent: true }); await tick(); };
  return h;
}

describe('createConversation', () => {
  it('starts out listening', () => {
    const h = harness();
    h.conversation.begin();
    expect(h.phase()).toBe('listening');
  });

  it('speech becomes a message', async () => {
    const h = harness();
    await h.speak(SPEECH);
    expect(h.sends.map((t) => t.text)).toEqual(['Hello there']);
    expect(h.sends[0].options.heard).toBeUndefined();
    expect(h.phase()).toBe('thinking');
  });

  // The voice gate holds the person to the level of their last turn Whisper accepted (gate.js): noise teaches nothing.
  it('a clip that is speech teaches the voice gate, and one that isn\'t teaches it nothing', async () => {
    const h = harness();
    h.conversation.begin();
    const noise = vi.fn();
    const speech = vi.fn();
    h.results.push(NOISE, SPEECH);
    h.conversation.speechStarted();
    h.conversation.speechEnded(new Float32Array(160), noise);
    h.conversation.speechStarted();
    h.conversation.speechEnded(new Float32Array(160), speech);
    await tick();
    expect(noise).not.toHaveBeenCalled();
    expect(speech).toHaveBeenCalledTimes(1);
  });

  it('noise sends nothing and goes back to listening', async () => {
    const h = harness();
    await h.speak(NOISE);
    expect(h.send).not.toHaveBeenCalled();
    expect(h.phase()).toBe('listening');
    expect([...h.clips.values()][0].verdict).toBe('noise');
  });

  it('speaks the reply, then listens again', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('Hi there. All good?');
    await h.finishReply();
    expect(h.audio.src).toBe('blob:Hi there.');
    expect(h.phase()).toBe('speaking');
    h.audio.finish();
    await tick();
    expect(h.audio.src).toBe('blob:All good?');
    h.audio.finish();
    await tick();
    expect(h.phase()).toBe('listening');
  });

  it('speech during a reply pauses it at once', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. Second part. ');
    h.conversation.speechStarted();
    expect(h.audio.paused).toBe(true);
    expect(h.phase()).toBe('hearing');
  });

  it('a false start changes nothing', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. ');
    h.conversation.speechFailed();
    await tick();
    expect(h.audio.paused).toBe(false);
    expect(h.transcribe).toHaveBeenCalledTimes(1);
  });

  it('a cough during a reply resumes it from the same place', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. Second part. ');
    h.audio.currentTime = 0.8;
    await h.speak(NOISE);
    expect(h.audio.paused).toBe(false);
    expect(h.audio.src).toBe('blob:First part.');
    expect(h.audio.currentTime).toBe(0.8);
    expect(h.send).toHaveBeenCalledTimes(1);
    expect(h.abort).not.toHaveBeenCalled();
  });

  it('a real interruption stops the reply and sends how much was heard', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. Second part. Third part. ');
    h.audio.finish();
    await tick();
    h.audio.currentTime = 0.1;
    await h.speak({ ...SPEECH, text: 'Wait a moment' });
    expect(h.abort).toHaveBeenCalledTimes(1);
    expect(h.sends[1].text).toBe('Wait a moment');
    expect(h.sends[1].options.heard).toEqual({ messageId: 'a-1', chars: 11 });
    expect(h.audio.paused).toBe(true);
  });

  it('cutting in before a word has played leaves none of the reply', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. ');
    h.audio.currentTime = 0.1;
    await h.speak({ ...SPEECH, text: 'Actually no' });
    expect(h.sends[1].options.heard).toEqual({ messageId: 'a-1', chars: 0 });
  });

  it('cutting in before the reply has even started has nothing to cut', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.speak({ ...SPEECH, text: 'Actually no' });
    expect(h.sends[1].options.heard).toBeUndefined();
  });

  it('a reply that played to its end is left whole', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('All of it. ');
    await h.finishReply();
    h.audio.finish();
    await tick();
    await h.speak({ ...SPEECH, text: 'Next question' });
    expect(h.sends[1].options.heard).toBeUndefined();
    expect(h.abort).not.toHaveBeenCalled();
  });

  it('tapping the circle stops the reply and listens', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. Second part. ');
    h.conversation.cutIn();
    await tick();
    expect(h.abort).toHaveBeenCalledTimes(1);
    expect(h.audio.paused).toBe(true);
    expect(h.phase()).toBe('listening');
  });

  it('what was heard before the tap goes with the next thing said', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. Second part. Third part. ');
    h.audio.finish();
    await tick();
    h.audio.currentTime = 0.1;
    h.conversation.cutIn();
    await tick();
    await h.speak({ ...SPEECH, text: 'Something else' });
    expect(h.sends[1].text).toBe('Something else');
    expect(h.sends[1].options.heard).toEqual({ messageId: 'a-1', chars: 11 });
    expect(h.abort).toHaveBeenCalledTimes(1);
  });

  it('only the next message carries it', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. ');
    h.conversation.cutIn();
    await tick();
    await h.speak({ ...SPEECH, text: 'One' });
    await h.reply('Answer. ', 'a-2');
    await h.finishReply();
    h.audio.finish();
    await tick();
    await h.speak({ ...SPEECH, text: 'Two' });
    expect(h.sends.map((t) => t.options.heard)).toEqual([undefined, { messageId: 'a-1', chars: 0 }, undefined]);
  });

  it('a tap with nothing playing changes nothing', async () => {
    const h = harness();
    h.conversation.begin();
    h.conversation.cutIn();
    await tick();
    expect(h.abort).not.toHaveBeenCalled();
    expect(h.phase()).toBe('listening');
  });

  it('words that arrive together become one message', async () => {
    const h = harness();
    h.results.push({ ...SPEECH, text: 'First words' }, { ...SPEECH, text: 'and the rest' });
    h.conversation.speechStarted();
    h.conversation.speechEnded(new Float32Array(160));
    h.conversation.speechStarted();
    h.conversation.speechEnded(new Float32Array(160));
    await tick();
    expect(h.sends.map((t) => t.text)).toEqual(['First words and the rest']);
  });

  it('holds its words while the person is still talking', async () => {
    const h = harness();
    h.results.push({ ...SPEECH, text: 'First words' });
    h.conversation.speechStarted();
    h.conversation.speechEnded(new Float32Array(160));
    h.conversation.speechStarted();
    await tick();
    expect(h.send).not.toHaveBeenCalled();
    h.conversation.speechFailed();
    await tick();
    expect(h.sends.map((t) => t.text)).toEqual(['First words']);
  });

  it('words that arrive while the last reply is still stopping join the next message', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. ');
    h.abort.mockImplementation(() => {});   // the stopped turn takes a moment to settle
    await h.speak({ ...SPEECH, text: 'One thing' });
    await h.speak({ ...SPEECH, text: 'and another' });
    expect(h.send).toHaveBeenCalledTimes(1);
    h.sends[0].settle({ sent: true });
    await tick();
    expect(h.sends.map((t) => t.text)).toEqual(['Hello there', 'One thing and another']);
  });

  it('its own voice coming back through the microphone is dropped', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('The capital of France is Paris. ');
    await h.speak({ ...SPEECH, text: 'capital of France is Paris' });
    expect(h.send).toHaveBeenCalledTimes(1);
    expect(h.audio.paused).toBe(false);
    expect([...h.clips.values()].at(-1).verdict).toBe('echo');
  });

  it('saying the reply\'s own words after it ended is a real question, not an echo', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('The sky is blue because of scattering. ');
    await h.finishReply();
    h.audio.finish();
    await tick();
    h.time += 5000;
    await h.speak({ ...SPEECH, text: 'Why is the sky blue' });
    expect(h.sends.map((t) => t.text)).toEqual(['Hello there', 'Why is the sky blue']);
  });

  it('its last words, still arriving as the reply ends, are an echo all the same', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('The capital of France is Paris. ');
    await h.finishReply();
    h.conversation.speechStarted();
    h.audio.finish();
    await tick();
    h.results.push({ ...SPEECH, text: 'capital of France is Paris' });
    h.conversation.speechEnded(new Float32Array(16000));
    await tick();
    expect(h.send).toHaveBeenCalledTimes(1);
    expect([...h.clips.values()].at(-1).verdict).toBe('echo');
  });

  it('a failed transcription lets the reply carry on', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. ');
    await h.speak(new Error('Voice input is busy.'));
    expect(h.audio.paused).toBe(false);
    const clip = [...h.clips.values()].at(-1);
    expect(clip.verdict).toBe('failed');
    expect(clip.error).toBe('Voice input is busy.');
  });

  it('a refused message goes back to listening', async () => {
    const h = harness();
    await h.speak(SPEECH);
    h.sends[0].settle({ sent: false });
    await tick();
    expect(h.phase()).toBe('listening');
  });

  it('records how long each step took', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('Hi there. ');
    const clip = [...h.clips.values()][0];
    expect(clip.endedAt).toBeLessThan(clip.transcribedAt);
    expect(clip.transcribedAt).toBeLessThan(clip.firstTextAt);
    expect(clip.firstTextAt).toBeLessThan(clip.firstSentenceAt);
    expect(clip.firstSentenceAt).toBeLessThan(clip.firstAudioAt);
    expect(clip.scores).toEqual({ noSpeech: 0.05, avgLogprob: -0.3 });
  });

  // Spec 2026-11-04-speech-choices: What it heard says which language and model heard each clip.
  it('a clip keeps the language and the model that heard it', async () => {
    const h = harness();
    await h.speak({ ...SPEECH, language: 'si', model: 'whisper-small-si' });
    const clip = [...h.clips.values()][0];
    expect([clip.heardIn, clip.heardBy]).toEqual(['si', 'whisper-small-si']);
  });

  it('end stops everything, and a late answer changes nothing', async () => {
    const h = harness();
    await h.speak(SPEECH);
    await h.reply('First part. ');
    let late;
    h.transcribe.mockImplementation(() => new Promise((resolve) => { late = resolve; }));
    h.conversation.speechStarted();
    h.conversation.speechEnded(new Float32Array(160));
    await tick();
    h.conversation.end();
    expect(h.abort).toHaveBeenCalled();
    expect(h.audio.paused).toBe(true);
    late(SPEECH);
    await tick();
    expect(h.send).toHaveBeenCalledTimes(1);
    expect(h.phase()).toBe('off');
  });
});
