import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import ReadSelectionButton from './ReadSelectionButton';

// Walk finding 3: the floating Read Selection button sat over the chat composer's Send button, at 1366x900 and
// at 390x844 (elementFromPoint at Send's centre returned the button), so a mouse or touch user couldn't send.
// jsdom has no layout, so this pins the condition: where the button is drawn at all, and where it is placed.
// The measured check at both sizes is in the Task 22b report.
const base = (over = {}) => ({
    hasDocument: true, isReadingSelection: false, darkMode: false, inChat: false,
    onReadSelection: vi.fn(), onStopSelectionRead: vi.fn(), onAskAboutSelection: vi.fn(), ...over,
});
const wrapper = () => screen.getByTitle(/Select text and click to read it aloud|Stop reading/).closest('div.fixed');

afterEach(() => { vi.restoreAllMocks(); });

describe('ReadSelectionButton', () => {
    // App renders this button and is not under test, so a refactor that drops `inChat` would bring the overlap on
    // Send back without any test failing. The prop is required: in development (Vite's dev server and vitest both
    // set import.meta.env.DEV) a missing one stops the render, so the app fails on its first load instead of only
    // misbehaving in the Chat view. In a production build the person gets the pre-fix behaviour, not a blank page.
    describe('the inChat prop is required', () => {
        it('runs under DEV here, so the throw tests below are the development branch', () => {
            expect(import.meta.env.DEV).toBe(true);
        });

        const refuses = (over) => {
            vi.spyOn(console, 'error').mockImplementation(() => {});   // React logs the error it rethrows
            const { inChat: _dropped, ...props } = base(over);
            expect(() => render(<ReadSelectionButton {...props} />)).toThrow(/inChat/);
        };

        it('throws when it is not passed', () => refuses());
        it('throws when it is not passed, even without a document (the first render of the app)', () => refuses({ hasDocument: false }));
        it('throws when it is not a boolean', () => {
            vi.spyOn(console, 'error').mockImplementation(() => {});
            expect(() => render(<ReadSelectionButton {...base({ inChat: 'chat' })} />)).toThrow(/inChat/);
        });
        it('in a production build it logs once and renders as if inChat were false', () => {
            vi.stubEnv('DEV', false);
            try {
                const error = vi.spyOn(console, 'error').mockImplementation(() => {});
                const { inChat: _dropped, ...props } = base();
                const { rerender } = render(<ReadSelectionButton {...props} />);
                expect(screen.getByRole('button', { name: 'Read Selection' })).toBeInTheDocument();   // the reader's place: drawn
                expect(error).toHaveBeenCalledTimes(1);
                expect(error.mock.calls[0][0]).toMatch(/inChat/);
                rerender(<ReadSelectionButton {...props} isReadingSelection />);   // the app re-renders constantly: no flood
                expect(screen.getByTitle('Stop reading').closest('div.fixed').className).toMatch(/bottom-24/);
                expect(error).toHaveBeenCalledTimes(1);
            } finally {
                vi.unstubAllEnvs();
            }
        });

        it('in a production build a non-boolean also renders as if inChat were false', () => {
            vi.stubEnv('DEV', false);
            try {
                vi.spyOn(console, 'error').mockImplementation(() => {});
                render(<ReadSelectionButton {...base({ inChat: 'chat' })} />);
                expect(screen.getByRole('button', { name: 'Read Selection' })).toBeInTheDocument();
            } finally {
                vi.unstubAllEnvs();
            }
        });

        it('takes false and true', () => {
            expect(() => render(<ReadSelectionButton {...base({ inChat: false })} />)).not.toThrow();
            expect(() => render(<ReadSelectionButton {...base({ inChat: true })} />)).not.toThrow();
        });
    });

    it('shows Read Selection (and Ask AI) over the reader', () => {
        render(<ReadSelectionButton {...base()} />);
        expect(screen.getByRole('button', { name: 'Read Selection' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Ask AI' })).toBeInTheDocument();
    });

    it('is not drawn in the Chat view, where it would cover the Send button', () => {
        const { container } = render(<ReadSelectionButton {...base({ inChat: true, onAskAboutSelection: null })} />);
        expect(screen.queryByRole('button', { name: 'Read Selection' })).toBeNull();
        expect(container).toBeEmptyDOMElement();
    });

    it('is not drawn without a document', () => {
        const { container } = render(<ReadSelectionButton {...base({ hasDocument: false })} />);
        expect(container).toBeEmptyDOMElement();
    });

    it('a selection being read keeps its Stop control in the Chat view, at the top and away from the composer', () => {
        render(<ReadSelectionButton {...base({ inChat: true, isReadingSelection: true })} />);
        expect(screen.getByText('Reading...')).toBeInTheDocument();
        expect(screen.getByTitle('Stop reading')).toBeInTheDocument();
        const cls = wrapper().className;
        expect(cls).toMatch(/(^|\s)top-20(\s|$)/);
        expect(cls).not.toMatch(/bottom-/);
    });

    it('a selection being read floats at the bottom in the reader, as before', () => {
        render(<ReadSelectionButton {...base({ isReadingSelection: true })} />);
        const cls = wrapper().className;
        expect(cls).toMatch(/(^|\s)bottom-24(\s|$)/);
        expect(cls).not.toMatch(/(^|\s)top-/);
    });

    it('beside the chat panel it sits one panel width in from the right edge, at any width, not over the panel', () => {
        render(<ReadSelectionButton {...base({ besideChatPanel: true })} />);
        const cls = wrapper().className;
        expect(cls).toMatch(/(^|\s)right-\[424px\](\s|$)/);
        expect(cls).toMatch(/(^|\s)xl:right-\[464px\](\s|$)/);
        expect(cls).not.toMatch(/(^|\s)(md:)?right-(4|6)(\s|$)/);
    });

    it('with no panel it keeps its usual place', () => {
        render(<ReadSelectionButton {...base()} />);
        expect(wrapper().className).toMatch(/(^|\s)right-4(\s|$)/);
    });

    it('the Read Selection button still reads the selection', async () => {
        const props = base();
        render(<ReadSelectionButton {...props} />);
        screen.getByRole('button', { name: 'Read Selection' }).click();
        expect(props.onReadSelection).toHaveBeenCalledTimes(1);
    });
});
