import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import ChatView from './ChatView';
import { listen } from '../lib/speech/listen';

vi.mock('../lib/speech/listen', () => ({ listen: vi.fn() }));
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) })) }));

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const WINDOW = { vad_start: 0.5, vad_stay: 0.35, min_speech_ms: 250, pre_roll_ms: 300, silence_ms: 700, language: 'en' };
const TALK = {
  status: { enabled: true, ready: true, window: WINDOW }, synthesizeText: vi.fn(async () => null), voice: 'af_heart',
  speed: 1, volume: 1, requestTimeout: 15, onStarted: vi.fn(), sessionId: 's-1',
};
const mic = () => ({ destroy: vi.fn(), pause: vi.fn(async () => {}), start: vi.fn(async () => {}) });

function Harness(over) {
  const [draft, setDraft] = useState('');
  return (
    <ChatView theme={theme} darkMode={false} effectiveIsMobile={false} messages={[]} isStreaming={false}
      selectedModel="ollama:m" reachable sendMessage={vi.fn()} stopStream={vi.fn()} speakingMessageId={null}
      speakMessage={vi.fn()} stopSpeaking={vi.fn()} downloadingMessageId={null} downloadMessageAudio={vi.fn()}
      showToast={vi.fn()} pins={[]} onRemovePin={vi.fn()} numCtx={null} draft={draft} setDraft={setDraft}
      apiHost="" apiPort="" {...over} />
  );
}
const talkButton = () => screen.queryByRole('button', { name: 'Talk' });
async function startTalking(over = {}) {
  const opened = mic();
  listen.mockResolvedValue(opened);
  const view = render(<Harness talk={TALK} {...over} />);
  await act(async () => { fireEvent.click(talkButton()); });
  return { ...view, opened };
}

beforeEach(() => { vi.clearAllMocks(); });

describe('ChatView: Talk', () => {
  it('someone who may talk finds Talk beside the typing box', () => {
    render(<Harness talk={TALK} />);
    expect(talkButton()).toBeEnabled();
  });

  it('someone who may not finds no Talk at all', () => {
    render(<Harness talk={null} />);
    expect(talkButton()).not.toBeInTheDocument();
  });

  it('Talk swaps the typing box for the conversation, at once', async () => {
    await startTalking();
    expect(screen.getByRole('status')).toHaveTextContent('Listening');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(TALK.onStarted).toHaveBeenCalled();
  });

  it('End brings the typing box back, with the focus in it', async () => {
    const { opened } = await startTalking();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'End' })); });
    expect(opened.destroy).toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toHaveFocus();
  });

  // Settings, Talk: what the person changed on this device, over the server's defaults.
  it('listens with the person\'s own Talk settings over the server\'s', async () => {
    await startTalking({ talk: { ...TALK, choices: { gate: true, gate_db: 6, auto_gain: false, silence_ms: 1200 } } });
    expect(listen.mock.calls[0][0].settings).toMatchObject({
      ...WINDOW, gate: true, gate_db: 6, auto_gain: false, silence_ms: 1200, noise_suppression: true,
    });
    expect(screen.getByText('Ignoring voices 6 dB quieter than yours')).toBeInTheDocument();
  });

  it('with no choices, exactly what the server says, the gate off', async () => {
    await startTalking();
    expect(listen.mock.calls[0][0].settings).toMatchObject({ ...WINDOW, gate: false, auto_gain: true });
  });

  it('the Talk settings are a tap away while talking', async () => {
    const onOpenSettings = vi.fn();
    await startTalking({ talk: { ...TALK, onOpenSettings } });
    fireEvent.click(screen.getByRole('button', { name: 'Talk settings' }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  // The Speaking switch sits where the person is talking, not only in Settings (spec 2026-11-04-speech-choices).
  const SPEAKING_STATUS = {
    enabled: true, ready: true, engine: 'local', heard_by: 'this server', window: WINDOW,
    languages: [{ code: 'en', name: 'English' }, { code: 'si', name: 'Sinhala' }],
    hears: [{ code: 'si', name: 'Sinhala', model: 'whisper-small-si', label: 'Whisper small, Sinhala (run12)' }],
    default_model: { id: 'small', label: 'Whisper small' },
  };

  it('the Talk panel has the Speaking switch, and a tap sets the Speech language', async () => {
    const setSpeechLanguage = vi.fn();
    await startTalking({ talk: { ...TALK, status: SPEAKING_STATUS }, speechLanguage: 'auto', setSpeechLanguage });
    fireEvent.click(screen.getByRole('button', { name: 'Speaking: Auto (English)' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Sinhala/ }));
    expect(setSpeechLanguage).toHaveBeenCalledWith('si');
  });

  it('the message box has the Speaking switch beside the microphone', async () => {
    const { apiFetch } = await import('../utils/apiFetch');
    apiFetch.mockImplementation(async (h, p, path) => (path === '/v1/speech/status'
      ? { ok: true, status: 200, json: async () => SPEAKING_STATUS }
      : { ok: false, status: 404, json: async () => ({}) }));
    const setSpeechLanguage = vi.fn();
    render(<Harness talk={TALK} speechLanguage="si" setSpeechLanguage={setSpeechLanguage} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Speaking: Sinhala' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /English/ }));
    expect(setSpeechLanguage).toHaveBeenCalledWith('en');
  });

  // On a phone the message box's row has no room: the switch sits in the row of chips just above it.
  it('on a phone the Speaking switch sits above the message box, not squeezing it', async () => {
    const { apiFetch } = await import('../utils/apiFetch');
    apiFetch.mockImplementation(async (h, p, path) => (path === '/v1/speech/status'
      ? { ok: true, status: 200, json: async () => SPEAKING_STATUS }
      : { ok: false, status: 404, json: async () => ({}) }));
    render(<Harness talk={TALK} effectiveIsMobile speechLanguage="auto" setSpeechLanguage={vi.fn()} />);
    const chip = await screen.findByRole('button', { name: 'Speaking: Auto' });
    expect(chip.closest('[data-testid="composer-chips"]')).not.toBeNull();
    expect(screen.getAllByRole('button', { name: /^Speaking: / })).toHaveLength(1);
  });

  it('the circle moves with the voice', async () => {
    await startTalking();
    act(() => { listen.mock.calls[0][0].onLevel(0.6); });
    expect(document.querySelector('[style*="--level"]').style.getPropertyValue('--level')).toBe('0.6');
  });

  it('opening another chat ends the conversation there', async () => {
    const { opened, rerender } = await startTalking();
    await act(async () => { rerender(<Harness talk={{ ...TALK, sessionId: 's-2' }} />); });
    expect(opened.destroy).toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('a new chat getting its id from the first thing said is the same chat', async () => {
    const { opened, rerender } = await startTalking({ talk: { ...TALK, sessionId: null } });
    await act(async () => { rerender(<Harness talk={{ ...TALK, sessionId: 's-9' }} />); });
    expect(opened.destroy).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('Listening');
  });

  it('losing the right to talk ends it', async () => {
    const { opened, rerender } = await startTalking();
    await act(async () => { rerender(<Harness talk={null} />); });
    expect(opened.destroy).toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('waits while a typed reply is still being written', () => {
    render(<Harness talk={TALK} isStreaming />);
    expect(talkButton()).toBeDisabled();
  });
});
