import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import MarkdownReader from './MarkdownReader';
import { apiFetch } from '../utils/apiFetch';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));

const theme = { text: '', textMuted: '' };
let observers;

beforeEach(() => {
  observers = [];
  globalThis.IntersectionObserver = class {
    constructor(callback, options) { this.callback = callback; this.options = options; observers.push(this); }
    observe() {}
    disconnect() {}
  };
  Element.prototype.scrollIntoView = () => {};
});

describe('MarkdownReader page sync', () => {
  it('turns the page when a page of any height scrolls into the reading band', async () => {
    apiFetch.mockResolvedValue({ ok: true, text: async () => '<!-- page 1 -->\n\nOne\n\n<!-- page 2 -->\n\nTwo' });
    const setCurrentPage = vi.fn();
    render(<MarkdownReader theme={theme} darkMode={false} apiHost="" apiPort="" docId="abc" currentPage={1}
      setCurrentPage={setCurrentPage} scale={1} />);
    const two = (await screen.findByText('Two')).closest('[data-md-page]');
    const observer = observers.at(-1);
    // The band is a tenth of the window high, so a page taller than ten bands never has a tenth of itself in
    // it: any overlap must count, or scrolling a long page never turns the page.
    expect(observer.options.threshold).toBe(0);
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 5000);   // past the quiet time after the opening scroll
    observer.callback([{ isIntersecting: true, target: two, boundingClientRect: { top: 100 } }]);
    expect(setCurrentPage).toHaveBeenCalledWith(2);
  });
});
