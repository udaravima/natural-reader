import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ReadSelectionButton from './ReadSelectionButton';

// Walk finding 3: the floating Read Selection button sat over the chat composer's Send button, at 1366x900 and
// at 390x844 (elementFromPoint at Send's centre returned the button), so a mouse or touch user couldn't send.
// jsdom has no layout, so this pins the condition: where the button is drawn at all, and where it is placed.
// The measured check at both sizes is in the Task 22b report.
const base = (over = {}) => ({
    hasDocument: true, isReadingSelection: false, darkMode: false,
    onReadSelection: vi.fn(), onStopSelectionRead: vi.fn(), onAskAboutSelection: vi.fn(), ...over,
});
const wrapper = () => screen.getByTitle(/Select text and click to read it aloud|Stop reading/).closest('div.fixed');

describe('ReadSelectionButton', () => {
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
