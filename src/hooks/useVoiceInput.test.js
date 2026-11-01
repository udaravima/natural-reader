import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useVoiceInput } from './useVoiceInput';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';

const STATUS = (over = {}) => ({
  enabled: true, ready: true, state: 'ready', max_seconds: 120,
  languages: [{ code: 'de', name: 'German' }, { code: 'en', name: 'English' }], ...over,
});
const answer = (status, body) => ({
  ok: status >= 200 && status < 300, status,
  json: async () => { if (body === undefined) throw new Error('not json'); return body; },
});

class FakeRecorder {
  static instances = [];
  static bytes = 4;
  static mime = 'audio/webm;codecs=opus';
  static isTypeSupported = vi.fn(() => true);
  constructor(stream, opts) {
    this.stream = stream; this.opts = opts; this.state = 'inactive';
    this.mimeType = FakeRecorder.mime;
    FakeRecorder.instances.push(this);
  }
  start() { this.state = 'recording'; }
  stop() {
    if (this.state === 'inactive') return;
    this.state = 'inactive';
    if (FakeRecorder.bytes) this.ondataavailable?.({ data: new Blob([new Uint8Array(FakeRecorder.bytes)]) });
    this.onstop?.();
  }
}

let tracks;
let getUserMedia;
const makeStream = () => {
  tracks = [{ stop: vi.fn() }, { stop: vi.fn() }];
  return { getTracks: () => tracks };
};
const allTracksStopped = () => tracks.every((t) => t.stop.mock.calls.length >= 1);

let posts;
let onPost;
let statusBody;
const mount = (opts = {}) => {
  const onText = vi.fn();
  const showToast = vi.fn();
  const view = renderHook((props) => useVoiceInput({ apiHost: '', apiPort: '', language: 'auto', onText, showToast, ...props }), { initialProps: opts });
  return { ...view, onText, showToast };
};
const ready = async (opts) => {
  const m = mount(opts);
  await waitFor(() => expect(m.result.current.available).toBe(true));
  return m;
};
const startRec = async (m) => {
  await act(async () => { m.result.current.start(); });
  await waitFor(() => expect(m.result.current.phase).toBe('recording'));
};

beforeEach(() => {
  vi.clearAllMocks();
  FakeRecorder.instances = []; FakeRecorder.bytes = 4; FakeRecorder.mime = 'audio/webm;codecs=opus';
  FakeRecorder.isTypeSupported = vi.fn(() => true);
  vi.stubGlobal('MediaRecorder', FakeRecorder);
  getUserMedia = vi.fn(async () => makeStream());
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true, writable: true });
  posts = []; statusBody = STATUS();
  onPost = () => answer(200, { text: 'hello there', language: 'en', duration_s: 1 });
  apiFetch.mockImplementation(async (h, p, path, opts) => {
    if (path === '/v1/speech/status') return answer(200, statusBody);
    if (path === '/v1/speech/transcriptions') { posts.push(opts); return onPost(opts); }
    return answer(404, {});
  });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('useVoiceInput: status', () => {
  it('is available when status says enabled, with no reason', async () => {
    const m = await ready();
    expect(m.result.current.reason).toBeNull();
    expect(m.result.current.phase).toBe('idle');
  });
  it('is not available when status says enabled:false', async () => {
    statusBody = STATUS({ enabled: false });
    const m = mount();
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    await act(async () => {});
    expect(m.result.current.available).toBe(false);
  });
  it('is not available when the status call fails', async () => {
    apiFetch.mockImplementation(async () => { throw new Error('down'); });
    const m = mount();
    await act(async () => {});
    expect(m.result.current.available).toBe(false);
  });
  it('stays available while the model is still loading (ready:false)', async () => {
    statusBody = STATUS({ ready: false, state: 'loading' });
    const m = await ready();
    expect(m.result.current.available).toBe(true);
  });
});

describe('useVoiceInput: reasons', () => {
  it('names the missing secure connection', async () => {
    window.isSecureContext = false;
    const m = await ready();
    expect(m.result.current.reason).toBe('Voice input needs a secure (https) connection');
  });
  it("names a browser with no MediaRecorder", async () => {
    vi.stubGlobal('MediaRecorder', undefined);
    const m = await ready();
    expect(m.result.current.reason).toBe("This browser can't record audio");
  });
  it('a blocked microphone toasts, becomes the reason, and a second press asks again', async () => {
    getUserMedia.mockRejectedValueOnce(Object.assign(new Error('x'), { name: 'NotAllowedError' }));
    const m = await ready();
    await act(async () => { m.result.current.start(); });
    const text = "Microphone blocked: allow it in the browser's site settings";
    expect(m.showToast).toHaveBeenCalledWith(text, expect.anything());
    expect(m.result.current.reason).toBe(text);
    expect(m.result.current.phase).toBe('idle');
    await startRec(m);   // pressable again: the browser is asked again and now allows
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(m.result.current.reason).toBeNull();
  });
  it('NotFoundError toasts and returns to idle', async () => {
    getUserMedia.mockRejectedValueOnce(Object.assign(new Error('x'), { name: 'NotFoundError' }));
    const m = await ready();
    await act(async () => { m.result.current.start(); });
    expect(m.showToast).toHaveBeenCalledWith('No microphone was found.', expect.anything());
    expect(m.result.current.phase).toBe('idle');
  });
});

describe('useVoiceInput: recording', () => {
  it('records with webm opus when supported, else the browser default', async () => {
    const m = await ready();
    await startRec(m);
    expect(FakeRecorder.instances[0].opts).toEqual({ mimeType: 'audio/webm;codecs=opus' });
    const m2 = await ready();
    FakeRecorder.isTypeSupported = vi.fn(() => false);
    await startRec(m2);
    expect(FakeRecorder.instances[1].opts).toBeUndefined();
  });
  it('counts seconds, and stops by itself at 120 and uploads', async () => {
    const m = await ready();
    vi.useFakeTimers({ shouldAdvanceTime: false });
    await act(async () => { m.result.current.start(); await vi.advanceTimersByTimeAsync(0); });
    expect(m.result.current.phase).toBe('recording');
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(m.result.current.seconds).toBe(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(117000); });
    expect(posts).toHaveLength(1);
    expect(allTracksStopped()).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(m.onText).toHaveBeenCalledWith('hello there', 'en');
    expect(m.result.current.seconds).toBe(120);
  });
  it('freezes the timer while transcribing', async () => {
    const m = await ready();
    let release; onPost = () => new Promise((r) => { release = r; });
    vi.useFakeTimers();
    await act(async () => { m.result.current.start(); await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await act(async () => { m.result.current.stop(); });
    expect(m.result.current.phase).toBe('transcribing');
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(m.result.current.seconds).toBe(2);
    await act(async () => { release(answer(200, { text: 'x', language: 'en' })); });
  });
  it('Stop uploads once and hands the text over; double Stop uploads once', async () => {
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); m.result.current.stop(); });
    await waitFor(() => expect(m.onText).toHaveBeenCalledWith('hello there', 'en'));
    expect(posts).toHaveLength(1);
    expect(m.result.current.phase).toBe('idle');
    expect(allTracksStopped()).toBe(true);
  });
  it('two presses in one tick make one recorder', async () => {
    const m = await ready();
    await act(async () => { m.result.current.start(); m.result.current.start(); });
    await waitFor(() => expect(m.result.current.phase).toBe('recording'));
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(FakeRecorder.instances).toHaveLength(1);
  });
});

describe('useVoiceInput: upload', () => {
  const field = (i, name) => posts[i].body.get(name);
  it('sends the recording as recording.webm with the language setting', async () => {
    const m = await ready({ language: 'de' });
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].method).toBe('POST');
    expect(field(0, 'file').name).toBe('recording.webm');
    expect(field(0, 'language')).toBe('de');
  });
  it('auto sends no language field', async () => {
    const m = await ready({ language: 'auto' });
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body.has('language')).toBe(false);
  });
  it('a stored language no longer in the list is sent as auto', async () => {
    const m = await ready({ language: 'xx' });
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body.has('language')).toBe(false);
  });
  it.each([['audio/ogg;codecs=opus', 'recording.ogg'], ['audio/mp4', 'recording.m4a'], ['audio/webm', 'recording.webm']])(
    'names a %s recording %s', async (mime, name) => {
      FakeRecorder.mime = mime;
      const m = await ready();
      await startRec(m);
      await act(async () => { m.result.current.stop(); });
      await waitFor(() => expect(posts).toHaveLength(1));
      expect(field(0, 'file').name).toBe(name);
    });
  it('an empty text toasts "No speech was heard" and inserts nothing', async () => {
    onPost = () => answer(200, { text: '  ', language: 'en' });
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.showToast).toHaveBeenCalledWith('No speech was heard', expect.anything()));
    expect(m.onText).not.toHaveBeenCalled();
  });
  it('a recording with no bytes uploads nothing and toasts', async () => {
    FakeRecorder.bytes = 0;
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.showToast).toHaveBeenCalledWith('No speech was heard', expect.anything()));
    expect(posts).toHaveLength(0);
    expect(m.result.current.phase).toBe('idle');
  });
  it("shows the server's message for a 503", async () => {
    const msg = 'Voice input is still getting ready, try again in a minute';
    onPost = () => answer(503, { detail: msg });
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.showToast).toHaveBeenCalledWith(msg, expect.anything()));
    expect(m.result.current.phase).toBe('idle');
  });
  it("falls back to \"The transcription server didn't answer\" for a non-JSON body and for a network failure", async () => {
    const fallback = "The transcription server didn't answer";
    onPost = () => answer(502, undefined);
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.showToast).toHaveBeenCalledWith(fallback, expect.anything()));
    m.showToast.mockClear();
    onPost = () => { throw new TypeError('network'); };
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.showToast).toHaveBeenCalledWith(fallback, expect.anything()));
  });
  it('a 404 with voice still on toasts that it is unavailable', async () => {
    onPost = () => answer(404, { detail: 'off' });
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.showToast).toHaveBeenCalledWith("Voice input isn't available right now.", expect.anything()));
    expect(m.result.current.available).toBe(true);
    expect(m.result.current.phase).toBe('idle');
  });
  it('a 404 with voice now off makes the button go away with no toast', async () => {
    onPost = () => answer(404, { detail: 'off' });
    const m = await ready();
    await startRec(m);
    statusBody = STATUS({ enabled: false });
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.result.current.available).toBe(false));
    expect(m.showToast).not.toHaveBeenCalled();
  });
  it('a 404 re-reads the status and the button goes away', async () => {
    onPost = () => answer(404, { detail: 'off' });
    const m = await ready();
    await startRec(m);
    statusBody = STATUS({ enabled: false });
    const before = apiFetch.mock.calls.filter((c) => c[2] === '/v1/speech/status').length;
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.result.current.available).toBe(false));
    expect(apiFetch.mock.calls.filter((c) => c[2] === '/v1/speech/status').length).toBe(before + 1);
    expect(m.onText).not.toHaveBeenCalled();
  });
});

describe('useVoiceInput: stop edge cases', () => {
  it('a recorder the browser already stopped (microphone unplugged) still uploads, not stuck on Transcribing', async () => {
    const m = await ready();
    await startRec(m);
    const rec = FakeRecorder.instances[0];
    rec.state = 'inactive';
    rec.chunks = true;
    rec.stop = () => { throw new Error('InvalidStateError'); };
    // The browser delivered its data before it stopped.
    rec.ondataavailable({ data: new Blob([new Uint8Array(4)]) });
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(m.onText).toHaveBeenCalledWith('hello there', 'en'));
    expect(m.result.current.phase).toBe('idle');
    expect(allTracksStopped()).toBe(true);
  });
  it('a clock set backwards never shows a negative time', async () => {
    const m = await ready();
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
    await act(async () => { m.result.current.start(); await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { vi.setSystemTime(Date.now() - 60000); vi.advanceTimersByTime(1000); });
    expect(m.result.current.seconds).toBe(0);
  });
  it('the 120 s limit follows the wall clock: a throttled timer cannot run past it', async () => {
    const m = await ready();
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
    await act(async () => { m.result.current.start(); await vi.advanceTimersByTimeAsync(0); });
    expect(m.result.current.phase).toBe('recording');
    // One late tick, 121 s of real time after the start: only one interval callback fires.
    await act(async () => { vi.setSystemTime(Date.now() + 121000); vi.advanceTimersByTime(1000); });
    expect(posts).toHaveLength(1);
    expect(m.result.current.seconds).toBe(120);
  });
});

describe('useVoiceInput: cancel and release', () => {
  const slowPost = () => { let signal; onPost = (opts) => { signal = opts.signal; return new Promise(() => {}); }; return () => signal; };

  it('Cancel while recording releases the microphone and uploads nothing', async () => {
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.cancel(); });
    expect(allTracksStopped()).toBe(true);
    expect(posts).toHaveLength(0);
    expect(m.result.current.phase).toBe('idle');
  });
  it('Cancel while transcribing aborts the request, and a late answer changes nothing', async () => {
    let release; let signal;
    onPost = (opts) => { signal = opts.signal; return new Promise((r) => { release = r; }); };
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(posts).toHaveLength(1));
    await act(async () => { m.result.current.cancel(); });
    expect(signal.aborted).toBe(true);
    expect(m.result.current.phase).toBe('idle');
    await act(async () => { release(answer(200, { text: 'late', language: 'en' })); });
    expect(m.onText).not.toHaveBeenCalled();
    expect(m.showToast).not.toHaveBeenCalled();
  });
  it('an aborted request is not an error: no toast', async () => {
    onPost = (opts) => new Promise((_, rej) => opts.signal.addEventListener('abort', () => rej(Object.assign(new Error('a'), { name: 'AbortError' }))));
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await act(async () => { m.result.current.cancel(); });
    expect(m.showToast).not.toHaveBeenCalled();
  });
  it('Stop then Cancel aborts', async () => {
    const sig = slowPost();
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); m.result.current.cancel(); });
    await waitFor(() => expect(sig()?.aborted ?? true).toBe(true));
    expect(m.onText).not.toHaveBeenCalled();
  });
  it('Cancel before the microphone answers releases the stream that then arrives', async () => {
    let resolveGum;
    getUserMedia.mockImplementationOnce(() => new Promise((r) => { resolveGum = r; }));
    const m = await ready();
    await act(async () => { m.result.current.start(); });
    await act(async () => { m.result.current.cancel(); });
    await act(async () => { resolveGum(makeStream()); });
    expect(allTracksStopped()).toBe(true);
    expect(FakeRecorder.instances).toHaveLength(0);
    expect(m.result.current.phase).toBe('idle');
  });
  it('Escape cancels while recording', async () => {
    const m = await ready();
    await startRec(m);
    await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    expect(m.result.current.phase).toBe('idle');
    expect(allTracksStopped()).toBe(true);
    expect(posts).toHaveLength(0);
  });
  it('Escape cancels while transcribing and aborts', async () => {
    let signal; onPost = (o) => { signal = o.signal; return new Promise(() => {}); };
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(posts).toHaveLength(1));
    await act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    expect(signal.aborted).toBe(true);
    expect(m.result.current.phase).toBe('idle');
  });
  it('Escape does nothing while idle', async () => {
    const m = await ready();
    const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    await act(async () => { document.dispatchEvent(e); });
    expect(m.result.current.phase).toBe('idle');
    expect(e.defaultPrevented).toBe(false);
  });
  it('a recorder error releases the microphone, toasts, and returns to idle', async () => {
    const m = await ready();
    await startRec(m);
    await act(async () => { FakeRecorder.instances[0].onerror?.({ error: new Error('x') }); });
    expect(allTracksStopped()).toBe(true);
    expect(m.showToast).toHaveBeenCalledWith('The recording failed.', expect.anything());
    expect(m.result.current.phase).toBe('idle');
    expect(posts).toHaveLength(0);
  });
  it('Stop releases the microphone', async () => {
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    expect(allTracksStopped()).toBe(true);
  });
  it('unmounting while recording releases the microphone, uploads nothing', async () => {
    const m = await ready();
    await startRec(m);
    m.unmount();
    expect(allTracksStopped()).toBe(true);
    await act(async () => {});
    expect(posts).toHaveLength(0);
    expect(m.onText).not.toHaveBeenCalled();
  });
  it('unmounting while transcribing aborts, and nothing is inserted afterwards', async () => {
    let release; let signal;
    onPost = (o) => { signal = o.signal; return new Promise((r) => { release = r; }); };
    const m = await ready();
    await startRec(m);
    await act(async () => { m.result.current.stop(); });
    await waitFor(() => expect(posts).toHaveLength(1));
    m.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { release(answer(200, { text: 'late', language: 'en' })); });
    expect(m.onText).not.toHaveBeenCalled();
  });
  it('unmounting before the microphone answers releases the stream that then arrives', async () => {
    let resolveGum;
    getUserMedia.mockImplementationOnce(() => new Promise((r) => { resolveGum = r; }));
    const m = await ready();
    await act(async () => { m.result.current.start(); });
    m.unmount();
    await act(async () => { resolveGum(makeStream()); });
    expect(allTracksStopped()).toBe(true);
    expect(FakeRecorder.instances).toHaveLength(0);
  });
});
