import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ShareDialog from './ShareDialog';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const doc = { doc_id: 'd1', file_name: 'Owned.pdf' };

function api(over = {}) {
  return {
    shares: vi.fn(async () => [{ user_id: 'u1', name: 'Ann Lee', username: 'annl', shared_at: '2026-10-06T00:00:00Z' }]),
    share: vi.fn(async () => null),
    unshare: vi.fn(async () => null),
    lookup: vi.fn(async () => [{ id: 'u1', name: 'Ann Lee', status: 'active' }, { id: 'u2', name: 'Bob Ray', status: 'active' }]),
    ...over,
  };
}

describe('ShareDialog', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists who I shared with and hides them from the picker', async () => {
    const a = api();
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByText('Ann Lee')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Share Owned.pdf with'), { target: { value: 'ra' } });
    expect(await screen.findByRole('button', { name: /Bob Ray/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Ann Lee/ })).toBeNull();
  });

  it('shares with a picked person and reloads the list', async () => {
    const a = api();
    const showToast = vi.fn();
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={showToast} onClose={vi.fn()} />);
    await screen.findByText('Ann Lee');
    fireEvent.change(screen.getByLabelText('Share Owned.pdf with'), { target: { value: 'ra' } });
    fireEvent.click(await screen.findByRole('button', { name: /Bob Ray/ }));
    await waitFor(() => expect(a.share).toHaveBeenCalledWith('d1', 'u2'));
    expect(showToast).toHaveBeenCalledWith('Shared Owned.pdf with Bob Ray.', 3000);
    expect(a.shares).toHaveBeenCalledTimes(2);
  });

  it('stops sharing with someone', async () => {
    const a = api();
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={vi.fn()} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText('Stop sharing with Ann Lee'));
    await waitFor(() => expect(a.unshare).toHaveBeenCalledWith('d1', 'u1'));
  });
});
