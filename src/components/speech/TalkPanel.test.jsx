import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import TalkPanel, { TalkButton } from './TalkPanel';
import { clipTimes, medianFirstSound } from '../../lib/speech/clipTimes';

const theme = { bg: '', bgTertiary: '', border: '', text: '', textSecondary: '' };
const speech = (over = {}) => ({
  phase: 'listening', clips: [], muted: false, end: vi.fn(), toggleMute: vi.fn(), cutIn: vi.fn(), ...over,
});
const draw = (over = {}) => render(<TalkPanel theme={theme} speech={speech()} silenceMs={700} {...over} />);
const CLIP = {
  id: 1, speechMs: 1500, endedAt: 1000, transcribedAt: 2000, firstTextAt: 2500, firstSentenceAt: 2800,
  firstAudioAt: 3200, verdict: 'speech', text: 'Hello there', scores: { noSpeech: 0.04, avgLogprob: -0.31 },
};

describe('TalkPanel', () => {
  it('names what it is doing, for a screen reader too', () => {
    draw({ speech: speech({ phase: 'hearing' }) });
    expect(screen.getByRole('status')).toHaveTextContent('Hearing you');
  });

  it('offers Mute and End', () => {
    const end = vi.fn();
    const toggleMute = vi.fn();
    draw({ speech: speech({ end, toggleMute }) });
    fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
    fireEvent.click(screen.getByRole('button', { name: 'End' }));
    expect(toggleMute).toHaveBeenCalled();
    expect(end).toHaveBeenCalled();
  });

  it('says when muted, and that it can\'t hear', () => {
    draw({ speech: speech({ muted: true }) });
    expect(screen.getByRole('status')).toHaveTextContent('Muted');
    expect(screen.getByText(/can.t hear you/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Unmute' })).toBeInTheDocument();
  });

  it('takes the focus when it opens, so Escape works at once', () => {
    draw();
    expect(screen.getByRole('group', { name: 'Talk' })).toHaveFocus();
  });

  it('Escape ends the conversation', () => {
    const end = vi.fn();
    draw({ speech: speech({ end }) });
    fireEvent.keyDown(screen.getByRole('group', { name: 'Talk' }), { key: 'Escape' });
    expect(end).toHaveBeenCalled();
  });

  it('while it speaks, a tap on the circle cuts in', () => {
    const cutIn = vi.fn();
    draw({ speech: speech({ phase: 'speaking', cutIn }) });
    expect(screen.getByText(/tap the circle/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cut in' }));
    expect(cutIn).toHaveBeenCalled();
  });

  it('while it thinks, a tap stops that too', () => {
    draw({ speech: speech({ phase: 'thinking' }) });
    expect(screen.getByRole('button', { name: 'Cut in' })).toBeInTheDocument();
  });

  it('while it listens, there is nothing to cut', () => {
    draw();
    expect(screen.queryByRole('button', { name: 'Cut in' })).not.toBeInTheDocument();
  });

  // The way out when a room's noise holds a turn open: the circle, so nothing moves under a thumb mid-sentence.
  it('while it hears you, a tap on the circle sends what you said', () => {
    const send = vi.fn();
    draw({ speech: speech({ phase: 'hearing', send }) });
    expect(screen.getByText(/tap the circle to send/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Send now' }));
    expect(send).toHaveBeenCalled();
  });

  it('muted, it hears nothing, so there is nothing to send', () => {
    draw({ speech: speech({ phase: 'hearing', muted: true }) });
    expect(screen.queryByRole('button', { name: 'Send now' })).not.toBeInTheDocument();
  });

  it('while it listens, there is nothing to send yet', () => {
    draw();
    expect(screen.queryByRole('button', { name: 'Send now' })).not.toBeInTheDocument();
  });

  // A phone may keep its own gain whatever the app asks, and that weakens the voice gate: the numbers say so.
  it('says when the browser kept automatic gain on, and copies it with the numbers', () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    draw({ speech: speech({ clips: [CLIP], microphone: { autoGainControl: true } }) });
    expect(screen.getByText(/automatic gain on: this browser kept it/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Copy numbers' }));
    expect(JSON.parse(writeText.mock.calls[0][0])).toMatchObject({ microphone: { autoGainControl: true } });
  });

  // A long conversation's clips mustn't push the chat off the screen: the list scrolls inside a fixed height.
  it('what it heard scrolls inside itself, and keeps the newest clip in view', () => {
    const clips = [1, 2, 3].map((id) => ({ ...CLIP, id }));
    const { rerender } = draw({ speech: speech({ clips }) });
    const list = screen.getByTestId('clip-1').parentElement;
    expect(list.className).toMatch(/max-h-/);
    expect(list.className).toMatch(/overflow-y-auto/);
    Object.defineProperty(list, 'scrollHeight', { value: 900, configurable: true });
    rerender(<TalkPanel theme={theme} speech={speech({ clips: [...clips, { ...CLIP, id: 4 }] })} silenceMs={700} />);
    expect(list.scrollTop).toBe(900);
  });

  it('says nothing about gain when the browser turned it off as asked', () => {
    draw({ speech: speech({ clips: [CLIP], microphone: { autoGainControl: false } }) });
    expect(screen.queryByText(/automatic gain/i)).not.toBeInTheDocument();
  });

  it('asks for the microphone while it starts', () => {
    draw({ speech: speech({ phase: 'starting' }) });
    expect(screen.getByRole('status')).toHaveTextContent('Starting');
    expect(screen.getByText(/allow the microphone/i)).toBeInTheDocument();
  });

  it('says who hears the person when it isn\'t this server', () => {
    draw({ heardBy: 'Google Cloud' });
    expect(screen.getByText('Heard by Google Cloud')).toBeInTheDocument();
  });

  it('says nothing of the kind when this server hears them', () => {
    draw({ heardBy: 'this server' });
    expect(screen.queryByText(/heard by/i)).not.toBeInTheDocument();
  });

  it('the circle is the element the voice level moves', () => {
    const orbRef = { current: null };
    draw({ orbRef });
    expect(orbRef.current).toBeInstanceOf(HTMLElement);
  });

  it('shows each clip with what was decided and how long each step took', () => {
    draw({ speech: speech({ clips: [CLIP] }) });
    fireEvent.click(screen.getByText(/what it heard/i));
    const row = screen.getByTestId('clip-1');
    expect(row).toHaveTextContent('Hello there');
    expect(row).toHaveTextContent('Speech');
    expect(row).toHaveTextContent('0.04');
    expect(row).toHaveTextContent('Whisper 1.00 s');
    expect(row).toHaveTextContent('First sound 2.90 s');
  });
});

describe('TalkButton', () => {
  const button = (over = {}) => render(
    <TalkButton theme={theme} status={{ enabled: true, ready: true }} selectedModel="ollama:m" busy={false}
      onStart={vi.fn()} {...over} />,
  );

  it('starts talking at a press', () => {
    const onStart = vi.fn();
    button({ onStart });
    fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
    expect(onStart).toHaveBeenCalled();
  });

  it.each([
    ['the speech model is loading', { status: { enabled: true, ready: false, state: 'loading' } }, /getting ready/i],
    ['the speech engine is broken', { status: { enabled: true, ready: false, state: 'error' } }, /ask your admin/i],
    ['no model is chosen', { selectedModel: '' }, /pick a model/i],
    ['a reply is still being written', { busy: true }, /reply to finish/i],
  ])('waits while %s, and says why', (_, over, why) => {
    button(over);
    const talk = screen.getByRole('button', { name: 'Talk' });
    expect(talk).toBeDisabled();
    expect(talk.title).toMatch(why);
  });
});

describe('clipTimes', () => {
  it('counts from when the person stopped talking, silence included, in seconds', () => {
    expect(clipTimes(CLIP, 700)).toEqual({
      whisper: 1, model: 0.8, voice: 0.4, firstSound: 2.9,
    });
  });

  it('a clip that never reached the reply has no first sound', () => {
    expect(clipTimes({ endedAt: 1000, transcribedAt: 1800 }, 700)).toMatchObject({ whisper: 0.8, firstSound: null });
  });
});

describe('medianFirstSound', () => {
  it('takes the middle of the clips that got a reply', () => {
    const at = (ms) => ({ ...CLIP, firstAudioAt: 1000 + ms });
    expect(medianFirstSound([at(1000), at(3000), at(2000), { endedAt: 1 }], 0)).toBe(2);
  });

  it('with no replies there is nothing to say', () => {
    expect(medianFirstSound([], 700)).toBeNull();
  });
});
