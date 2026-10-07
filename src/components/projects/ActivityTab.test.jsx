import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ActivityTab, { describeEvent, timeAgo } from './ActivityTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const asha = { id: 'a', name: 'Asha' };
const ben = { id: 'b', name: 'Ben' };
const ev = (kind, details = {}, over = {}) => ({ id: 1, at: '2026-10-06T00:00:00Z', kind, actor: asha, subject: null, doc: null, details, ...over });

describe('describeEvent', () => {
  it.each([
    [ev('project.created', { name: 'Q3' }), 'Asha created the project'],
    [ev('project.created', { name: 'Q3' }, { subject: ben }), 'Asha created the project for Ben'],
    [ev('project.renamed', { from: 'Q3', to: 'Q4' }), 'Asha renamed the project from "Q3" to "Q4"'],
    [ev('project.described'), 'Asha changed the description'],
    [ev('member.added', { role: 'maintainer', via: 'member' }, { subject: ben }), 'Asha added Ben as Maintainer'],
    [ev('member.role_changed', { from: 'reader', to: 'owner' }, { subject: ben }), 'Asha changed Ben from Reader to Owner'],
    [ev('member.removed', { role: 'reader' }, { subject: null }), 'Asha removed a former member'],
    [ev('member.left', { role: 'reader' }, { actor: null }), 'A former member left the project'],
    [ev('document.added', { name: 'Q3.pdf' }, { doc: { id: 'd', name: 'Q3.pdf' } }), 'Asha added "Q3.pdf"'],
    [ev('document.removed', { name: 'Old.pdf' }, { doc: { id: 'd', name: 'Old.pdf' } }), 'Asha removed "Old.pdf"'],
  ])('%#', (event, text) => {
    expect(describeEvent(event)).toBe(text);
  });
});

describe('timeAgo', () => {
  const now = Date.parse('2026-10-06T12:00:00Z');
  it.each([
    ['2026-10-06T11:59:30Z', 'just now'], ['2026-10-06T11:15:00Z', '45 min ago'],
    ['2026-10-06T09:00:00Z', '3 h ago'], ['2026-10-04T12:00:00Z', '2 d ago'],
  ])('%s', (at, text) => {
    expect(timeAgo(at, now)).toBe(text);
  });
});

describe('ActivityTab', () => {
  it('lists events newest first and loads more with next_before', async () => {
    const api = {
      events: vi.fn(async (id, before) => (before
        ? { events: [ev('project.created', { name: 'Q3' }, { id: 1 })], next_before: null }
        : { events: [ev('project.described', {}, { id: 2 })], next_before: 2 })),
    };
    render(<ActivityTab theme={theme} api={api} project={{ id: 'p1' }} />);
    expect(await screen.findByText(/Asha changed the description/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText(/Asha created the project/)).toBeTruthy();
    expect(api.events).toHaveBeenLastCalledWith('p1', 2);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull());
  });

  it('says when there is no activity', async () => {
    render(<ActivityTab theme={theme} api={{ events: vi.fn(async () => ({ events: [], next_before: null })) }} project={{ id: 'p1' }} />);
    expect(await screen.findByText('No activity yet.')).toBeTruthy();
  });
});
