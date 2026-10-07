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

  it('says a found person is already shared with rather than that no account exists', async () => {
    const a = api({ lookup: vi.fn(async () => [{ id: 'u1', name: 'Ann Lee', status: 'active' }]) });
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText('Ann Lee');
    fireEvent.change(screen.getByLabelText('Share Owned.pdf with'), { target: { value: 'ann@example.com' } });
    expect(await screen.findByText('Already shared with them.')).toBeTruthy();
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

  it('shows a failed list load inline with Retry, not as an empty list', async () => {
    const a = api({
      shares: vi.fn()
        .mockRejectedValueOnce(new Error('Something went wrong on the server. Try again.'))
        .mockResolvedValueOnce([{ user_id: 'u1', name: 'Ann Lee', username: 'annl', shared_at: '2026-10-06T00:00:00Z' }]),
    });
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={vi.fn()} onClose={vi.fn()} />);
    expect((await screen.findByRole('alert')).textContent).toContain('Something went wrong on the server. Try again.');
    expect(screen.queryByText('Not shared with anyone yet.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Ann Lee')).toBeTruthy();
    expect(a.shares).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('toasts a failed share', async () => {
    const a = api({ share: vi.fn(async () => { throw new Error('User not found'); }) });
    const showToast = vi.fn();
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={showToast} onClose={vi.fn()} />);
    await screen.findByText('Ann Lee');
    fireEvent.change(screen.getByLabelText('Share Owned.pdf with'), { target: { value: 'ra' } });
    fireEvent.click(await screen.findByRole('button', { name: /Bob Ray/ }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('User not found', 5000));
  });
});
