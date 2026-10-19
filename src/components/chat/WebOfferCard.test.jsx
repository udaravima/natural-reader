import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WebOfferCard from './WebOfferCard';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const REASON = 'The files do not say when Zephyr shipped.';

function mount(over = {}) {
    const props = { theme, reason: REASON, query: 'zephyr release date', onSearch: vi.fn(), onDismiss: vi.fn(), disabled: false, ...over };
    return { props, ...render(<WebOfferCard {...props} />) };
}
const input = () => screen.getByRole('textbox', { name: 'Web search query' });
const searchButton = () => screen.getByRole('button', { name: 'Search the web' });

describe('WebOfferCard', () => {
    it('is a labelled region with a title, the reason, the query and both buttons', () => {
        mount();
        const region = screen.getByRole('region', { name: 'Web search offer' });
        expect(region).toHaveTextContent('Search the web?');
        expect(region).toHaveTextContent(REASON);
        expect(input()).toHaveValue('zephyr release date');
        expect(input()).toHaveAttribute('maxlength', '300');
        expect(searchButton()).toBeEnabled();
        expect(screen.getByRole('button', { name: 'Not now' })).toBeEnabled();
    });

    it('edits the query and searches with it', async () => {
        const user = userEvent.setup();
        const { props } = mount();
        await user.clear(input());
        await user.type(input(), '  zephyr 2.0 changelog  ');
        await user.click(searchButton());
        expect(props.onSearch).toHaveBeenCalledTimes(1);
        expect(props.onSearch).toHaveBeenCalledWith('zephyr 2.0 changelog');   // trimmed
    });

    it('searches with the prefilled query as it is', async () => {
        const user = userEvent.setup();
        const { props } = mount();
        await user.click(searchButton());
        expect(props.onSearch).toHaveBeenCalledWith('zephyr release date');
    });

    it('Enter in the input searches, the same as the button', async () => {
        const user = userEvent.setup();
        const { props } = mount();
        await user.clear(input());
        await user.type(input(), 'rhel packages{Enter}');
        expect(props.onSearch).toHaveBeenCalledTimes(1);
        expect(props.onSearch).toHaveBeenCalledWith('rhel packages');
    });

    it('Enter while an input method is composing does not search', () => {
        const { props } = mount();
        fireEvent.keyDown(input(), { key: 'Enter', isComposing: true });
        expect(props.onSearch).not.toHaveBeenCalled();
    });

    it('Not now hides the card and sends nothing', async () => {
        const user = userEvent.setup();
        const { props } = mount();
        await user.click(screen.getByRole('button', { name: 'Not now' }));
        expect(screen.queryByRole('region', { name: 'Web search offer' })).toBeNull();
        expect(props.onSearch).not.toHaveBeenCalled();
        expect(props.onDismiss).toHaveBeenCalledTimes(1);
    });

    it('Not now works without an onDismiss handler', async () => {
        const user = userEvent.setup();
        mount({ onDismiss: undefined });
        await user.click(screen.getByRole('button', { name: 'Not now' }));
        expect(screen.queryByRole('region', { name: 'Web search offer' })).toBeNull();
    });

    it('the dismissal lives in the card: a fresh card shows again', async () => {
        const user = userEvent.setup();
        const { unmount } = mount();
        await user.click(screen.getByRole('button', { name: 'Not now' }));
        unmount();
        mount();
        expect(screen.getByRole('region', { name: 'Web search offer' })).toBeInTheDocument();
    });

    describe('disabled while a reply is streaming', () => {
        it('disables the search button, and neither it nor Enter searches', async () => {
            const user = userEvent.setup();
            const { props } = mount({ disabled: true });
            expect(searchButton()).toBeDisabled();
            await user.click(searchButton());
            await user.type(input(), '{Enter}');
            expect(props.onSearch).not.toHaveBeenCalled();
        });

        it('still lets the user say "Not now"', async () => {
            const user = userEvent.setup();
            mount({ disabled: true });
            await user.click(screen.getByRole('button', { name: 'Not now' }));
            expect(screen.queryByRole('region')).toBeNull();
        });
    });

    describe('an empty query', () => {
        it('disables the search button, and Enter does nothing, until there is text', async () => {
            const user = userEvent.setup();
            const { props } = mount();
            await user.clear(input());
            expect(searchButton()).toBeDisabled();
            await user.type(input(), '   {Enter}');
            expect(searchButton()).toBeDisabled();           // spaces are not a query
            expect(props.onSearch).not.toHaveBeenCalled();
            await user.type(input(), 'x');
            expect(searchButton()).toBeEnabled();
        });

        it('starts empty and disabled when the note has no usable query', () => {
            for (const query of [undefined, null, '', '   ', 42, {}]) {
                const { unmount } = mount({ query });
                expect(input().value.trim()).toBe('');
                expect(searchButton()).toBeDisabled();
                unmount();
            }
        });
    });

    describe('the reason and the query are raw model text', () => {
        it('shows them as text, never as markup', () => {
            const bad = '<img src=x onerror=alert(1)> **bold** [x](javascript:alert(1))';
            const { container } = mount({ reason: bad, query: '<b>q</b>' });
            expect(container.querySelector('img, b, a')).toBeNull();
            expect(screen.getByText(bad)).toBeInTheDocument();
            expect(input()).toHaveValue('<b>q</b>');
        });

        it('strips control characters from both', () => {
            mount({ reason: 'No\u0000 rec\u0007ord\u001b[31m of\u0085 it.\u007f', query: 'ze\u0000phyr\u009f\u0007 re\u001flease' });
            expect(screen.getByRole('region')).toHaveTextContent('No record[31m of it.');
            expect(input()).toHaveValue('zephyr release');
        });

        it('strips tabs and line breaks too (no exceptions)', () => {
            mount({ reason: 'one\ntwo\r\nthree\tfour', query: 'a\nb' });
            expect(screen.getByRole('region')).toHaveTextContent('onetwothreefour');
            expect(input()).toHaveValue('ab');
        });

        it('keeps ordinary text, accents and emoji', () => {
            mount({ reason: 'Café résumé — 日本語 🙂', query: 'naïve 🙂' });
            expect(screen.getByText('Café résumé — 日本語 🙂')).toBeInTheDocument();
            expect(input()).toHaveValue('naïve 🙂');
        });

        it('caps a long reason at 300 characters with an ellipsis, and leaves a 300-character one whole', () => {
            const { unmount } = mount({ reason: 'r'.repeat(300) });
            expect(screen.getByText('r'.repeat(300))).toBeInTheDocument();
            unmount();
            mount({ reason: 'r'.repeat(5000) });
            expect(screen.getByText('r'.repeat(300) + '…')).toBeInTheDocument();
            expect(screen.queryByText('r'.repeat(301))).toBeNull();
        });

        it('does not cut an emoji in half when it caps the reason', () => {
            mount({ reason: '🙂'.repeat(400) });
            expect(screen.getByText('🙂'.repeat(300) + '…')).toBeInTheDocument();
        });

        it('prefills at most 300 characters of the query, so the box and the server agree', () => {
            mount({ query: 'q'.repeat(2000) });
            expect(input()).toHaveValue('q'.repeat(300));
        });

        it('does not cut a pair of surrogate halves in the prefilled query', () => {
            mount({ query: `${'q'.repeat(299)}🙂` });
            expect(input()).toHaveValue('q'.repeat(299));
        });

        it('searches with the stripped, capped query', async () => {
            const user = userEvent.setup();
            const { props } = mount({ query: `zeph\u0000yr ${'q'.repeat(2000)}` });
            await user.click(searchButton());
            expect(props.onSearch).toHaveBeenCalledWith(`zephyr ${'q'.repeat(293)}`);
        });

        it('shows no reason line when the reason is missing or only control characters', () => {
            for (const reason of [undefined, null, '', '\u0000\u0007', 42]) {
                const { unmount } = mount({ reason, query: 'q' });
                const text = screen.getByRole('region').textContent;
                expect(text).toContain('Search the web?');
                expect(text).not.toMatch(/undefined|null|42/);
                unmount();
            }
        });
    });
});
