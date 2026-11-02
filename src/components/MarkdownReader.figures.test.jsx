import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import MarkdownReader from './MarkdownReader';
import { apiFetch } from '../utils/apiFetch';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));

const theme = { text: '', textMuted: '' };
const props = { theme, darkMode: false, apiHost: '', apiPort: '', docId: 'abc', currentPage: 1, setCurrentPage: () => {}, scale: 1 };

beforeEach(() => {
  globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
  Element.prototype.scrollIntoView = () => {};
});

const opened = (text) => {
  apiFetch.mockResolvedValue({ ok: true, text: async () => text });
  return render(<MarkdownReader {...props} />);
};

describe('MarkdownReader pictures', () => {
  it('shows a kept picture from the document\'s own route', async () => {
    opened('<!-- page 1 -->\n\nBefore\n\n![Image](figures/2.png)\n\nAfter');
    const picture = await screen.findByRole('img', { name: 'Image' });
    expect(picture.getAttribute('src')).toBe('/v1/docs/abc/figures/2.png');
    expect(picture.getAttribute('loading')).toBe('lazy');
  });

  it('follows a configured API host, like every other request', async () => {
    apiFetch.mockResolvedValue({ ok: true, text: async () => '![Image](figures/1.png)' });
    render(<MarkdownReader {...props} apiHost="reader.example.com" apiPort="8000" />);
    const picture = await screen.findByRole('img', { name: 'Image' });
    expect(picture.getAttribute('src')).toBe('http://reader.example.com:8000/v1/docs/abc/figures/1.png');
  });

  it('leaves a picture that is not one of ours alone', async () => {
    const { container } = opened('![Chart](https://example.com/a.png)');
    const picture = await within(container).findByRole('img', { name: 'Chart' });
    expect(picture.getAttribute('src')).toBe('https://example.com/a.png');
  });
});
