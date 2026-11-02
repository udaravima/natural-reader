import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import ChatView from './ChatView';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const answer = (status, body) => ({ ok: status < 400, status, json: async () => body });
const STATUS = { enabled: true, ready: true, state: 'ready', max_seconds: 120, languages: [{ code: 'en', name: 'English' }] };

class FakeRecorder {
  static isTypeSupported = () => true;
  constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; }
  start() { this.state = 'recording'; }
  stop() {
    if (this.state === 'inactive') return;
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob([new Uint8Array(3)]) });
    this.onstop?.();
  }
}

let tracks; let onPost; let status;
const sendMessage = vi.fn();
const showToast = vi.fn();

function Harness({ initial = '', speechLanguage = 'auto', ...over }) {
  const [draft, setDraft] = useState(initial);
  return (
    <ChatView theme={theme} darkMode={false} effectiveIsMobile={false} messages={[]} isStreaming={false}
      selectedModel="ollama:m" reachable sendMessage={sendMessage} stopStream={vi.fn()} speakingMessageId={null}
      speakMessage={vi.fn()} stopSpeaking={vi.fn()} downloadingMessageId={null} downloadMessageAudio={vi.fn()}
      showToast={showToast} pins={[]} onRemovePin={vi.fn()} numCtx={null} draft={draft} setDraft={setDraft}
      apiHost="" apiPort="" speechLanguage={speechLanguage} {...over} />
  );
}
const box = () => screen.getByRole('textbox');
const mic = () => screen.getByRole('button', { name: 'Speak a message' });
const waitMic = () => screen.findByRole('button', { name: 'Speak a message' });

async function say(initial, caret, text = 'hello there') {
  onPost = () => answer(200, { text, language: 'en', duration_s: 1 });
  render(<Harness initial={initial} />);
  const ta = box();
  await waitMic();
  ta.focus();
  ta.setSelectionRange(caret, caret);
  fireEvent.click(mic());
  fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
  return ta;
}

beforeEach(() => {
  vi.clearAllMocks();
  status = STATUS;
  onPost = () => answer(200, { text: 'hello there', language: 'en' });
  tracks = [{ stop: vi.fn() }];
  vi.stubGlobal('MediaRecorder', FakeRecorder);
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: vi.fn(async () => ({ getTracks: () => tracks })) }, configurable: true });
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true, writable: true });
  apiFetch.mockImplementation(async (h, p, path, opts) => {
    if (path === '/v1/speech/status') return answer(200, status);
    if (path === '/v1/speech/transcriptions') return onPost(opts);
    return answer(404, {});
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('ChatView: the microphone button', () => {
  it('shows when voice input is on', async () => {
    render(<Harness />);
    expect(await waitMic()).toBeEnabled();
  });
  it('is not rendered at all when voice input is off', async () => {
    status = { ...STATUS, enabled: false };
    render(<Harness />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/speech/status'));
    await act(async () => {});
    expect(screen.queryByRole('button', { name: 'Speak a message' })).toBeNull();
  });
  it('is not rendered when the status call fails', async () => {
    apiFetch.mockImplementation(async () => { throw new Error('down'); });
    render(<Harness />);
    await act(async () => {});
    expect(screen.queryByRole('button', { name: 'Speak a message' })).toBeNull();
  });
  it('without https it is disabled and says why', async () => {
    window.isSecureContext = false;
    render(<Harness />);
    const b = await waitMic();
    expect(b).toBeDisabled();
    expect(b).toHaveAttribute('title', 'Voice input needs a secure (https) connection');
  });
  it("without MediaRecorder it is disabled: \"This browser can't record audio\"", async () => {
    vi.stubGlobal('MediaRecorder', undefined);
    render(<Harness />);
    const b = await waitMic();
    expect(b).toBeDisabled();
    expect(b).toHaveAttribute('title', "This browser can't record audio");
  });
  it('a blocked microphone toasts, shows the reason as title, and stays pressable', async () => {
    navigator.mediaDevices.getUserMedia.mockRejectedValueOnce(Object.assign(new Error('x'), { name: 'NotAllowedError' }));
    render(<Harness />);
    fireEvent.click(await waitMic());
    const text = "Microphone blocked: allow it in the browser's site settings";
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(text, expect.anything()));
    expect(mic()).toBeEnabled();
    expect(mic()).toHaveAttribute('title', text);
  });
  it('meets the 44 px phone target', async () => {
    render(<Harness />);
    expect((await waitMic()).className).toMatch(/min-h-11/);
    expect(mic().className).toMatch(/min-w-11/);
  });
});

describe('ChatView: recording and transcribing', () => {
  it('Start shows Stop with a 0:00 timer (aria-live off) and a Cancel', async () => {
    render(<Harness />);
    fireEvent.click(await waitMic());
    expect(await screen.findByRole('button', { name: 'Stop and transcribe' })).toBeInTheDocument();
    const timer = screen.getByText('0:00');
    expect(timer).toHaveAttribute('aria-live', 'off');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop and transcribe' }).className).toMatch(/min-h-11/);
    expect(screen.getByRole('button', { name: 'Cancel' }).className).toMatch(/min-h-11/);
  });
  it('Stop shows "Transcribing…" as a status with Cancel still there', async () => {
    onPost = () => new Promise(() => {});
    render(<Harness />);
    fireEvent.click(await waitMic());
    fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Transcribing…');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });
  it('puts the text at the cursor of "Say |now": "Say hello there now"; nothing is sent; focus returns', async () => {
    const ta = await say('Say now', 4);
    await waitFor(() => expect(ta.value).toBe('Say hello there now'));
    expect(document.activeElement).toBe(ta);
    expect(ta.selectionStart).toBe('Say hello there'.length);
    expect(ta.selectionEnd).toBe('Say hello there'.length);
    expect(sendMessage).not.toHaveBeenCalled();
  });
  it('adds a space only where it is missing', async () => {
    const ta = await say('Say  now', 4);   // two spaces: one already before, one after
    await waitFor(() => expect(ta.value).toBe('Say hello there now'));
  });
  it('into an empty box, adds no spaces', async () => {
    const ta = await say('', 0);
    await waitFor(() => expect(ta.value).toBe('hello there'));
  });
  it('at the end of the text, adds a space before and none after', async () => {
    const ta = await say('Say', 3);
    await waitFor(() => expect(ta.value).toBe('Say hello there'));
  });
  it('inserts at the caret as it is when the answer arrives, and keeps text typed meanwhile', async () => {
    let release; onPost = () => new Promise((r) => { release = r; });
    render(<Harness initial="Say now" />);
    const ta = box();
    fireEvent.click(await waitMic());
    fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
    await screen.findByRole('status');
    // While "Transcribing…" shows, the person types at the front and moves the caret after "Say".
    fireEvent.change(ta, { target: { value: 'Well. Say now' } });
    ta.setSelectionRange(9, 9);
    await act(async () => { release(answer(200, { text: 'hello there', language: 'en' })); });
    await waitFor(() => expect(ta.value).toBe('Well. Say hello there now'));
  });
  it('never replaces a selection', async () => {
    let release; onPost = () => new Promise((r) => { release = r; });
    render(<Harness initial="Say now" />);
    const ta = box();
    fireEvent.click(await waitMic());
    fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
    await screen.findByRole('status');
    ta.setSelectionRange(0, 3);   // "Say" selected
    await act(async () => { release(answer(200, { text: 'hi', language: 'en' })); });
    await waitFor(() => expect(ta.value).toBe('Say hi now'));
  });
  it('Cancel while transcribing aborts, and a late answer leaves the draft alone', async () => {
    let release; let signal;
    onPost = (o) => { signal = o.signal; return new Promise((r) => { release = r; }); };
    render(<Harness initial="Say now" />);
    fireEvent.click(await waitMic());
    fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
    await screen.findByRole('status');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(signal.aborted).toBe(true);
    await act(async () => { release(answer(200, { text: 'late', language: 'en' })); });
    expect(box().value).toBe('Say now');
    expect(await waitMic()).toBeInTheDocument();
  });
  it('on a phone the word "Transcribing…" is visually hidden but stays in the accessible tree', async () => {
    onPost = () => new Promise(() => {});
    render(<Harness />);
    fireEvent.click(await waitMic());
    fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
    const st = await screen.findByRole('status');
    expect(st).toHaveTextContent('Transcribing…');
    const word = [...st.querySelectorAll('span')].find((n) => n.textContent === 'Transcribing…');
    expect(word.className).toMatch(/\bsr-only\b/);
    expect(word.className).toMatch(/sm:not-sr-only/);
    expect(box().className).toMatch(/\bmin-w-0\b/);
  });
  it('Enter on Stop leaves focus on Cancel, and the answer moves it to the box', async () => {
    let ok; onPost = () => new Promise((r) => { ok = r; });
    render(<Harness />);
    fireEvent.click(await waitMic());
    const stopBtn = await screen.findByRole('button', { name: 'Stop and transcribe' });
    await waitFor(() => expect(document.activeElement).toBe(stopBtn));
    fireEvent.click(stopBtn);   // what Enter on a focused button does
    const cancelBtn = await screen.findByRole('button', { name: 'Cancel' });
    await waitFor(() => expect(document.activeElement).toBe(cancelBtn));
    await act(async () => { ok(answer(200, { text: 'hello', language: 'en' })); });
    await waitFor(() => expect(document.activeElement).toBe(box()));
  });
  it('Start moves focus to Stop, so Enter stops without the mouse', async () => {
    render(<Harness />);
    fireEvent.click(await waitMic());
    const stopBtn = await screen.findByRole('button', { name: 'Stop and transcribe' });
    await waitFor(() => expect(document.activeElement).toBe(stopBtn));
  });
  it('Cancel returns focus to the message box', async () => {
    render(<Harness />);
    fireEvent.click(await waitMic());
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(document.activeElement).toBe(box()));
  });
  it('Escape returns focus to the message box too', async () => {
    render(<Harness />);
    fireEvent.click(await waitMic());
    await screen.findByRole('button', { name: 'Stop and transcribe' });
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(document.activeElement).toBe(box()));
  });
  describe('focus is only taken when nobody else has it', () => {
    const withOther = (over) => render(<><Harness initial="Say now" {...over} /><button aria-label="other">x</button></>);
    const toTranscribing = async () => {
      fireEvent.click(await waitMic());
      fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
      await screen.findByRole('status');
      screen.getByLabelText('other').focus();
    };
    it('stays on another field after Cancel', async () => {
      onPost = () => new Promise(() => {});
      withOther();
      await toTranscribing();
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      await waitMic();
      expect(document.activeElement).toBe(screen.getByLabelText('other'));
    });
    it('stays on another field after a failure', async () => {
      let fail; onPost = () => new Promise((r) => { fail = r; });
      withOther();
      await toTranscribing();
      await act(async () => { fail(answer(503, { detail: { error: 'busy', message: 'busy' } })); });
      await waitMic();
      expect(document.activeElement).toBe(screen.getByLabelText('other'));
    });
    it('stays on another field after an answer, and the text is still inserted', async () => {
      let ok; onPost = () => new Promise((r) => { ok = r; });
      withOther();
      await toTranscribing();
      await act(async () => { ok(answer(200, { text: 'hello', language: 'en' })); });
      await waitFor(() => expect(box().value).toBe('hello Say now'));
      expect(document.activeElement).toBe(screen.getByLabelText('other'));
    });
    it('Stop does not pull focus from another field to Cancel', async () => {
      onPost = () => new Promise(() => {});
      withOther();
      fireEvent.click(await waitMic());
      fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
      screen.getByLabelText('other').focus();
      await screen.findByRole('status');
      expect(document.activeElement).toBe(screen.getByLabelText('other'));
    });
    it('with focus on nothing, the box takes it', async () => {
      onPost = () => new Promise(() => {});
      withOther();
      fireEvent.click(await waitMic());
      fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
      await screen.findByRole('status');
      document.activeElement?.blur();
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      await waitFor(() => expect(document.activeElement).toBe(box()));
    });
  });
  it('Escape cancels while recording', async () => {
    render(<Harness />);
    fireEvent.click(await waitMic());
    await screen.findByRole('button', { name: 'Stop and transcribe' });
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(await waitMic()).toBeInTheDocument();
    expect(tracks[0].stop).toHaveBeenCalled();
  });
  it('a 404 on the upload re-reads the status and the button disappears', async () => {
    onPost = () => answer(404, { detail: { error: 'voice_off', message: 'Voice input is off.' } });
    render(<Harness />);
    fireEvent.click(await waitMic());
    const b = await screen.findByRole('button', { name: 'Stop and transcribe' });
    status = { ...STATUS, enabled: false };
    fireEvent.click(b);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Speak a message' })).toBeNull());
    expect(screen.queryByRole('button', { name: 'Stop and transcribe' })).toBeNull();
  });
  it('sends the Speech language setting with the recording', async () => {
    const posts = [];
    onPost = (o) => { posts.push(o); return answer(200, { text: 'x', language: 'en' }); };
    render(<Harness speechLanguage="en" />);
    fireEvent.click(await waitMic());
    fireEvent.click(await screen.findByRole('button', { name: 'Stop and transcribe' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body.get('language')).toBe('en');
  });
  it('the old "intentionally unwired" note and VoiceRecorder file are gone', async () => {
    const fs = await import('node:fs');
    expect(fs.existsSync('src/components/VoiceRecorder.jsx')).toBe(false);
    expect(fs.readFileSync('src/components/ChatView.jsx', 'utf8')).not.toMatch(/intentionally not imported/);
  });
});
