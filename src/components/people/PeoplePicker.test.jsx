import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import PeoplePicker from './PeoplePicker';

const theme = { bg: '', border: '', text: '', textSecondary: '', textMuted: '', bgTertiary: '' };
const ann = { id: 'u1', name: 'Ann Lee', username: 'annl', status: 'active' };
const pen = { id: 'u2', name: 'Pen Pal', username: null, status: 'pending' };

function mount({ lookup = vi.fn(async () => [ann, pen]), excludeIds = [] } = {}) {
  const onPick = vi.fn();
  render(<PeoplePicker theme={theme} lookup={lookup} onPick={onPick} excludeIds={excludeIds} label="Find a person" />);
  return { lookup, onPick, box: screen.getByLabelText('Find a person') };
}

describe('PeoplePicker', () => {
  beforeEach(() => vi.clearAllMocks());

  it('waits for two characters and debounces typing into one lookup', async () => {
    const { lookup, box } = mount();
    fireEvent.change(box, { target: { value: 'a' } });
    fireEvent.change(box, { target: { value: 'an' } });
    fireEvent.change(box, { target: { value: 'ann' } });
    await screen.findByText('Ann Lee');
    expect(lookup).toHaveBeenCalledTimes(1);
    expect(lookup).toHaveBeenCalledWith('ann');
  });

  it('shows @username and "awaiting approval", and picking clears the box', async () => {
    const { onPick, box } = mount();
    fireEvent.change(box, { target: { value: 'an' } });
    expect(await screen.findByText('@annl')).toBeTruthy();
    expect(screen.getByText('awaiting approval')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Ann Lee/ }));
    expect(onPick).toHaveBeenCalledWith(ann);
    expect(box.value).toBe('');
    expect(screen.queryByText('Ann Lee')).toBeNull();
  });

  it('hides people in excludeIds', async () => {
    const { box } = mount({ excludeIds: ['u1'] });
    fireEvent.change(box, { target: { value: 'an' } });
    expect(await screen.findByText('Pen Pal')).toBeTruthy();
    expect(screen.queryByText('Ann Lee')).toBeNull();
  });

  it('says what to try when nobody is found', async () => {
    const { box } = mount({ lookup: vi.fn(async () => []) });
    fireEvent.change(box, { target: { value: 'zz' } });
    expect(await screen.findByText('Nobody found. Try their full email address.')).toBeTruthy();
    fireEvent.change(box, { target: { value: 'zz@example.com' } });
    expect(await screen.findByText('No account uses that email address.')).toBeTruthy();
  });

  it('shows a refused lookup as a notice', async () => {
    const { box } = mount({ lookup: vi.fn(async () => { throw new Error('Something went wrong on the server. Try again.'); }) });
    fireEvent.change(box, { target: { value: 'an' } });
    expect((await screen.findByRole('alert')).textContent).toBe('Something went wrong on the server. Try again.');
  });

  it('ignores a slow answer to an older query', async () => {
    let releaseOld;
    const lookup = vi.fn((q) => (q === 'an'
      ? new Promise((resolve) => { releaseOld = () => resolve([pen]); })
      : Promise.resolve([ann])));
    const { box } = mount({ lookup });
    fireEvent.change(box, { target: { value: 'an' } });
    await waitFor(() => expect(lookup).toHaveBeenCalledWith('an'));
    fireEvent.change(box, { target: { value: 'ann' } });
    await screen.findByText('Ann Lee');
    releaseOld();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText('Pen Pal')).toBeNull();
  });
});
