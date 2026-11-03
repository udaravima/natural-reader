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
