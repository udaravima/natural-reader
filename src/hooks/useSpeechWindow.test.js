import { describe, it, expect, vi, beforeEach, onTestFinished } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useSpeechStatus, useSpeechWindow, MIC_BLOCKED } from './useSpeechWindow';
import { listen } from '../lib/speech/listen';
import { apiFetch } from '../utils/apiFetch';

vi.mock('../lib/speech/listen', () => ({ listen: vi.fn() }));
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));

const SETTINGS = { max_no_speech: 0.4, min_logprob: -1, language: 'en' };

const mic = () => ({ destroy: vi.fn(), pause: vi.fn(async () => {}), start: vi.fn(async () => {}) });
const baseProps = (over = {}) => ({
  apiHost: '', apiPort: '8000', settings: SETTINGS, sendMessage: vi.fn(async () => ({ sent: true })),
  stopStream: vi.fn(), synthesizeText: vi.fn(async () => null), voice: 'af_heart', speed: 1, volume: 1,
  requestTimeout: 15, ...over,
});
const transcribed = (body) => ({ ok: true, status: 200, json: async () => body });

beforeEach(() => {
  vi.resetAllMocks();
  window.HTMLMediaElement.prototype.play = vi.fn(async () => {});
  window.HTMLMediaElement.prototype.pause = vi.fn();
});

describe('useSpeechWindow', () => {
  it('opens the microphone with the server\'s settings and starts listening', async () => {
    const opened = mic();
    listen.mockResolvedValue(opened);
    const onStarted = vi.fn();
    const { result } = renderHook(() => useSpeechWindow(baseProps({ onStarted })));
    expect(result.current.phase).toBe('off');
    await act(async () => { await result.current.start(); });
    expect(listen.mock.calls[0][0].settings).toBe(SETTINGS);
    expect(result.current.phase).toBe('listening');
    expect(onStarted).toHaveBeenCalled();
  });

  it('a clip goes to Whisper as WAV in the window\'s language, and speech becomes a turn', async () => {
    listen.mockResolvedValue(mic());
    apiFetch.mockResolvedValue(transcribed({ text: 'Hello there', no_speech: 0.02, avg_logprob: -0.2 }));
    const props = baseProps();
    const { result } = renderHook(() => useSpeechWindow(props));
    await act(async () => { await result.current.start(); });
    await act(async () => { listen.mock.calls[0][0].onSpeechEnded(new Float32Array(1600)); });
    await waitFor(() => expect(props.sendMessage).toHaveBeenCalled());
    const [path, request] = apiFetch.mock.calls[0].slice(2);
    expect(path).toBe('/v1/speech/transcriptions');
    expect(request.body.get('file').type).toBe('audio/wav');
    expect(request.body.get('language')).toBe('en');
    expect(props.sendMessage.mock.calls[0][0]).toBe('Hello there');
    expect(result.current.clips[0]).toMatchObject({ verdict: 'speech', text: 'Hello there' });
  });

  it('an empty language is left for Whisper to detect', async () => {
    listen.mockResolvedValue(mic());
    apiFetch.mockResolvedValue(transcribed({ text: '', no_speech: null, avg_logprob: null }));
    const { result } = renderHook(() => useSpeechWindow(baseProps({ settings: { ...SETTINGS, language: '' } })));
    await act(async () => { await result.current.start(); });
    await act(async () => { listen.mock.calls[0][0].onSpeechEnded(new Float32Array(1600)); });
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(apiFetch.mock.calls[0][3].body.has('language')).toBe(false);
  });

  it('End releases the microphone and stops the reply', async () => {
    const opened = mic();
    listen.mockResolvedValue(opened);
    const props = baseProps();
    const { result } = renderHook(() => useSpeechWindow(props));
    await act(async () => { await result.current.start(); });
    act(() => { result.current.end(); });
    expect(opened.destroy).toHaveBeenCalled();
    expect(props.stopStream).toHaveBeenCalled();
    expect(result.current.phase).toBe('off');
  });

  it('leaving the window releases the microphone', async () => {
    const opened = mic();
    listen.mockResolvedValue(opened);
    const { result, unmount } = renderHook(() => useSpeechWindow(baseProps()));
    await act(async () => { await result.current.start(); });
    unmount();
    expect(opened.destroy).toHaveBeenCalled();
  });

  it('a microphone that opens after the person left is released at once', async () => {
    const opened = mic();
    let open;
    listen.mockReturnValue(new Promise((resolve) => { open = resolve; }));
    const { result, unmount } = renderHook(() => useSpeechWindow(baseProps()));
    let starting;
    act(() => { starting = result.current.start(); });
    unmount();
    await act(async () => { open(opened); await starting; });
    expect(opened.destroy).toHaveBeenCalled();
  });

  it('a refused microphone says how to allow it and stays off', async () => {
    listen.mockRejectedValue(Object.assign(new Error('no'), { name: 'NotAllowedError' }));
    const showToast = vi.fn();
    const { result } = renderHook(() => useSpeechWindow(baseProps({ showToast })));
    await act(async () => { await result.current.start(); });
    expect(showToast).toHaveBeenCalledWith(MIC_BLOCKED, 5000);
    expect(result.current.phase).toBe('off');
  });

  it('Mute closes the microphone, and unmute opens it again', async () => {
    const opened = mic();
    listen.mockResolvedValue(opened);
    const { result } = renderHook(() => useSpeechWindow(baseProps()));
    await act(async () => { await result.current.start(); });
    await act(async () => { await result.current.toggleMute(); });
    expect(opened.pause).toHaveBeenCalled();
    expect(result.current.muted).toBe(true);
    await act(async () => { await result.current.toggleMute(); });
    expect(opened.start).toHaveBeenCalled();
    expect(result.current.muted).toBe(false);
  });

  it('a refused clip shows the server\'s words', async () => {
    listen.mockResolvedValue(mic());
    apiFetch.mockResolvedValue({
      ok: false, status: 503, json: async () => ({ detail: { message: 'Voice input is busy.' } }),
    });
    const { result } = renderHook(() => useSpeechWindow(baseProps()));
    await act(async () => { await result.current.start(); });
    await act(async () => { listen.mock.calls[0][0].onSpeechEnded(new Float32Array(1600)); });
    await waitFor(() => expect(result.current.clips[0]?.verdict).toBe('failed'));
    expect(result.current.clips[0].error).toBe('Voice input is busy.');
  });
});

describe('useSpeechStatus', () => {
  it('reads the status when it may be used', async () => {
    apiFetch.mockResolvedValue({ ok: true, json: async () => ({ enabled: true, ready: true, window: SETTINGS }) });
    const { result } = renderHook(() => useSpeechStatus({ apiHost: '', apiPort: '8000', allowed: true }));
    await waitFor(() => expect(result.current?.enabled).toBe(true));
  });

  it('asks again while the speech model loads, so nobody has to reload', async () => {
    vi.useFakeTimers();
    onTestFinished(() => vi.useRealTimers());
    apiFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ enabled: true, ready: false }) });
    apiFetch.mockResolvedValue({ ok: true, json: async () => ({ enabled: true, ready: true }) });
    const { result } = renderHook(() => useSpeechStatus({ apiHost: '', apiPort: '8000', allowed: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.ready).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(result.current.ready).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });

  it('asks nothing of a person who may not use it', () => {
    renderHook(() => useSpeechStatus({ apiHost: '', apiPort: '8000', allowed: false }));
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
