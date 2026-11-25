import { describe, it, expect, vi } from 'vitest';
import { render, screen, within, fireEvent } from '@testing-library/react';
import { CallTranscript, RefreshControl } from './PhoneCallParts';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const msg = (id, role, content) => ({ id, role, content, timestamp: 't', status: 'complete', finishReason: 'stop' });
const stat = (over) => ({
  stopped_at: 6.2, heard_at: 7.46, first_text_at: 8.0, first_sentence_at: 8.3, first_audio_at: 9.04, error: null,
  user_message_id: null, assistant_message_id: null, ...over,
});

describe('CallTranscript with message ids', () => {
  const messages = [
    msg('a0', 'assistant', 'Hello, how can I help?'),
    msg('u1', 'user', 'When do you open?'),
    msg('a1', 'assistant', 'We open at nine.'),
    msg('u2', 'user', 'And on Sunday?'),
    msg('a2', 'assistant', 'We are closed.'),
  ];
  const stats = [
    stat({ user_message_id: 'u1', assistant_message_id: 'a1' }),
    stat({ stopped_at: 30.0, heard_at: 30.8, first_audio_at: null, error: 'hearing' }),      // misheard, between the turns
    stat({ stopped_at: 65.9, heard_at: 67.0, first_audio_at: 69.0, error: 'voice', user_message_id: 'u2', assistant_message_id: 'a2' }),
  ];

  it('puts each turn\'s timing beside its own messages, and a misheard clip at its time', () => {
    render(<CallTranscript theme={theme} messages={messages} stats={stats} />);
    const rows = screen.getAllByTestId('phone-row').map((r) => r.textContent);
    expect(rows).toEqual([
      'Line: Hello, how can I help?',
      'Caller: When do you open?at 0:06',
      'Line: We open at nine.first sound 2.8 s · heard after 1.3 s',
      'Not heard at 0:30',
      'Caller: And on Sunday?at 1:05',
      'Line: We are closed.first sound 3.1 s · heard after 1.1 s · a sentence was skipped',
    ]);
    expect(screen.queryByTestId('phone-turn-timings')).toBeNull();
  });

  it('matches by id, not by position: a failed claim and a misheard clip in front never shift the timings', () => {
    const shifted = [
      stat({ stopped_at: 3.0, heard_at: 3.5, first_audio_at: null, error: 'begin' }),               // no messages at all
      stat({ stopped_at: 8.0, first_audio_at: null, error: 'hearing' }),
      stat({ stopped_at: 20.0, heard_at: 21.0, first_audio_at: 24.0, user_message_id: 'u1', assistant_message_id: 'a1' }),
    ];
    render(<CallTranscript theme={theme} messages={messages.slice(0, 3)} stats={shifted} />);
    const rows = screen.getAllByTestId('phone-row').map((r) => r.textContent);
    expect(rows).toContain('Caller: When do you open?at 0:20');
    expect(rows).toContain('Line: We open at nine.first sound 4.0 s · heard after 1.0 s');
    expect(rows).toContain('At 0:03: the answer failed');
    expect(rows).toContain('Not heard at 0:08');
    // the greeting has no turn, so nothing is attached to it
    expect(rows.indexOf('Not heard at 0:08')).toBeLessThan(rows.indexOf('Caller: When do you open?at 0:20'));
  });

  it('shows an interrupted turn with whatever timings it got, and a call still going with none', () => {
    const cut = [stat({ user_message_id: 'u1', assistant_message_id: 'a1', first_audio_at: null })];
    const { unmount } = render(<CallTranscript theme={theme} messages={messages.slice(0, 3)} stats={cut} />);
    expect(screen.getAllByTestId('phone-row')[2].textContent).toBe('Line: We open at nine.heard after 1.3 s');
    unmount();
    render(<CallTranscript theme={theme} messages={messages.slice(0, 3)} stats={[]} />);
    expect(screen.getAllByTestId('phone-row').map((r) => r.textContent)).toEqual([
      'Line: Hello, how can I help?', 'Caller: When do you open?', 'Line: We open at nine.']);
  });
});

describe('CallTranscript cut-off rows', () => {
  const reply = (status) => [msg('u1', 'user', 'When do you open?'), { ...msg('a1', 'assistant', ''), status }];
  const messages = reply('aborted');

  it.each([
    ['streaming', 'Line: …'],
    ['error', 'Line: (no answer)'],
    ['aborted', 'Line: (cut off before answering)'],
    ['complete', 'Line: (cut off before answering)'],
  ])('an empty reply whose message is %s reads %s', (status, text) => {
    render(<CallTranscript theme={theme} messages={reply(status)} stats={[]} />);
    expect(screen.getAllByTestId('phone-row')[1].textContent).toBe(text);
  });

  it('follows the message, not the call: an earlier cut reply in a live call is not "still being written"', () => {
    const live = [...reply('aborted'), msg('u2', 'user', 'Hello?'),
      { ...msg('a2', 'assistant', 'Yes, I am here.'), status: 'streaming' }];
    render(<CallTranscript theme={theme} messages={live} stats={[]} inProgress />);
    expect(screen.getAllByTestId('phone-row').map((r) => r.textContent)).toEqual([
      'Caller: When do you open?', 'Line: (cut off before answering)', 'Caller: Hello?', 'Line: Yes, I am here.']);
  });

  it('keeps its timing note beside the label, and "the answer failed" beside "(no answer)"', () => {
    const stats = [stat({ user_message_id: 'u1', assistant_message_id: 'a1', first_audio_at: null, error: 'provider_unavailable' })];
    render(<CallTranscript theme={theme} messages={reply('error')} stats={stats} />);
    expect(screen.getAllByTestId('phone-row')[1].textContent).toBe('Line: (no answer)heard after 1.3 s · the answer failed');
  });

  it('an entry with no messages and no error is a cut-in, not a failed answer', () => {
    const stats = [stat({ stopped_at: 1.4, error: null }), stat({ stopped_at: 20, user_message_id: 'u1', assistant_message_id: 'a1' })];
    render(<CallTranscript theme={theme} messages={messages} stats={stats} />);
    const rows = screen.getAllByTestId('phone-row').map((r) => r.textContent);
    expect(rows[0]).toBe('At 0:01: cut in before the answer began');
    expect(rows.join('|')).not.toContain('the answer failed');
  });
});

describe('CallTranscript for a call recorded before the ids', () => {
  it('keeps the separate list of timings', () => {
    const old = [
      { stopped_at: 6.2, heard_at: 7.46, first_text_at: 8.0, first_sentence_at: 8.3, first_audio_at: 9.04, error: null },
      { stopped_at: 12.0, heard_at: 12.9, first_text_at: null, first_sentence_at: null, first_audio_at: null, error: 'hearing' },
    ];
    render(<CallTranscript theme={theme} messages={[msg('m1', 'assistant', 'Hello.')]} stats={old} />);
    const list = screen.getByTestId('phone-turn-timings');
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Turn 1: first sound 2.8 s · heard after 1.3 s', 'Not heard']);
    expect(screen.queryByText(/^at \d/)).toBeNull();
  });
});

describe('RefreshControl', () => {
  it('reads Refresh, then Refreshing… (disabled) while busy, then when it was updated, in the zone', () => {
    const onRefresh = vi.fn();
    const { rerender } = render(<RefreshControl onRefresh={onRefresh} busy={false} updatedAt={null} zone="UTC" />);
    const button = screen.getByRole('button', { name: 'Refresh' });
    expect(button.disabled).toBe(false);
    expect(screen.queryByText(/Updated/)).toBeNull();
    fireEvent.click(button);
    expect(onRefresh).toHaveBeenCalledTimes(1);
    rerender(<RefreshControl onRefresh={onRefresh} busy updatedAt={null} zone="UTC" />);
    expect(screen.getByRole('button', { name: 'Refreshing…' }).disabled).toBe(true);
    rerender(<RefreshControl onRefresh={onRefresh} busy={false} updatedAt={new Date('2026-11-24T20:00:07Z')} zone="Asia/Colombo" />);
    expect(screen.getByRole('button', { name: 'Refresh' }).disabled).toBe(false);
    expect(screen.getByText('Updated 01:30:07')).toBeTruthy();
  });
});
