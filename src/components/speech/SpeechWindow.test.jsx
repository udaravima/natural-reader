import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SpeechWindow from './SpeechWindow';
import { clipTimes, medianFirstSound } from '../../lib/speech/clipTimes';

const theme = { bg: '', bgTertiary: '', border: '', text: '', textSecondary: '' };
const SETTINGS = { silence_ms: 700 };
const speech = (over = {}) => ({
  phase: 'off', clips: [], muted: false, start: vi.fn(), end: vi.fn(), toggleMute: vi.fn(), ...over,
});
const draw = (over = {}) => render(
  <SpeechWindow theme={theme} status={{ enabled: true, ready: true, window: SETTINGS }} speech={speech()}
    messages={[]} models={[]} selectedModel="ollama:m" setSelectedModel={vi.fn()} {...over} />,
);
const CLIP = {
  id: 1, speechMs: 1500, endedAt: 1000, transcribedAt: 2000, firstTextAt: 2500, firstSentenceAt: 2800,
  firstAudioAt: 3200, verdict: 'speech', text: 'Hello there', scores: { noSpeech: 0.04, avgLogprob: -0.31 },
};

describe('SpeechWindow', () => {
  it('asks for a press before it opens the microphone', () => {
    const start = vi.fn();
    draw({ speech: speech({ start }) });
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(start).toHaveBeenCalled();
  });

  it('names what it is doing, for a screen reader too', () => {
    draw({ speech: speech({ phase: 'hearing' }) });
    expect(screen.getByRole('status')).toHaveTextContent('Hearing you');
  });

  it('offers End and Mute while it runs', () => {
    const end = vi.fn();
    const toggleMute = vi.fn();
    draw({ speech: speech({ phase: 'listening', end, toggleMute }) });
    fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
    fireEvent.click(screen.getByRole('button', { name: 'End' }));
    expect(toggleMute).toHaveBeenCalled();
    expect(end).toHaveBeenCalled();
  });

  it('says when muted', () => {
    draw({ speech: speech({ phase: 'listening', muted: true }) });
    expect(screen.getByRole('status')).toHaveTextContent('Muted');
    expect(screen.getByRole('button', { name: 'Unmute' })).toBeInTheDocument();
  });

  it('says so when voice input is off, with no Start', () => {
    draw({ status: { enabled: false, ready: false } });
    expect(screen.getByText(/voice input is off/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument();
  });

  it('says so while the speech model loads, and holds Start back', () => {
    draw({ status: { enabled: true, ready: false, state: 'loading', window: SETTINGS } });
    expect(screen.getByText(/getting ready/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
  });

  it('holds Start back with no model to talk to', () => {
    draw({ models: [], selectedModel: '' });
    expect(screen.getByText(/pick a model/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
  });

  it('shows each clip with what was decided and how long each step took', () => {
    draw({ speech: speech({ clips: [CLIP] }), selectedModel: 'ollama:m' });
    fireEvent.click(screen.getByText(/what it heard/i));
    const row = screen.getByTestId('clip-1');
    expect(row).toHaveTextContent('Hello there');
    expect(row).toHaveTextContent('Speech');
    expect(row).toHaveTextContent('0.04');
    expect(row).toHaveTextContent('Whisper 1.00 s');
    expect(row).toHaveTextContent('First sound 2.90 s');
  });

  it('shows the words of the conversation', () => {
    draw({ messages: [{ id: 'u', role: 'user', content: 'Hello' }, { id: 'a', role: 'assistant', content: 'Hi.' }] });
    expect(screen.getByText('Hello')).toBeInTheDocument();
    expect(screen.getByText('Hi.')).toBeInTheDocument();
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
