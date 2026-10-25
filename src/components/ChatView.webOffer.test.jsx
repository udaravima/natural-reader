import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatView from './ChatView';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, messages: [], isStreaming: false,
    selectedModel: 'ollama:m', reachable: true, sendMessage: vi.fn(async () => ({ sent: true })), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(), downloadingMessageId: null,
    downloadMessageAudio: vi.fn(), showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null, ...over,
});
const user = (id, content = 'q') => ({ id, role: 'user', content });
const reply = (id, content, extra = {}) => ({ id, role: 'assistant', content, status: 'complete', ...extra });
const offer = (query = 'zephyr release date', reason = 'The files do not say when Zephyr shipped.') =>
    ({ docContext: { notes: [{ kind: 'web_offer', query, reason }] } });

const cards = () => screen.queryAllByRole('region', { name: 'Web search offer' });
const card = () => screen.getByRole('region', { name: 'Web search offer' });
const searchButton = () => within(card()).getByRole('button', { name: 'Search the web' });

describe('ChatView — the web offer card (C2.1 Task 18)', () => {
    it('draws the card under a reply that carries an offer note, as it would after a reload', () => {
        render(<ChatView {...baseProps({ messages: [user('u1'), reply('a1', 'Not in the files.', offer())] })} />);
        expect(screen.getByText('Not in the files.')).toBeInTheDocument();
        expect(card()).toHaveTextContent('Search the web?');
        expect(card()).toHaveTextContent('The files do not say when Zephyr shipped.');
        expect(within(card()).getByRole('textbox', { name: 'Web search query' })).toHaveValue('zephyr release date');
    });

    it('is the whole reply when the model wrote no text', () => {
        render(<ChatView {...baseProps({ messages: [user('u1'), reply('a1', '', offer())] })} />);
        expect(card()).toBeInTheDocument();
        expect(screen.queryByText('…')).toBeNull();                       // no filler bubble
        expect(screen.queryByRole('button', { name: 'Copy message' })).toBeNull();
    });

    it('draws no card for a reply without an offer, or for a user message', () => {
        render(<ChatView {...baseProps({ messages: [
            { ...user('u1'), docContext: { notes: [{ kind: 'web_offer', query: 'x', reason: 'y' }] } },
            reply('a1', 'Plain answer.'),
        ] })} />);
        expect(cards()).toHaveLength(0);
        expect(screen.queryByText('Offered a web search')).toBeNull();
    });

    it('only the last reply\'s offer has buttons', () => {
        render(<ChatView {...baseProps({ messages: [
            user('u1', 'when did Zephyr ship?'), reply('a1', '', offer('zephyr release date', 'old reason')),
            user('u2', 'and its parent?'), reply('a2', 'Not there either.', offer('zephyr parent company', 'new reason')),
        ] })} />);
        expect(cards()).toHaveLength(1);
        expect(card()).toHaveTextContent('new reason');
        expect(within(card()).getByRole('textbox', { name: 'Web search query' })).toHaveValue('zephyr parent company');
        // The earlier one is a plain line: no input, no buttons, no reason.
        expect(screen.getByText('Offered a web search')).toBeInTheDocument();
        expect(screen.getAllByRole('button', { name: 'Search the web' })).toHaveLength(1);
        expect(screen.getAllByRole('textbox', { name: 'Web search query' })).toHaveLength(1);
        expect(screen.queryByText('old reason')).toBeNull();
    });

    it('an offer loses its controls when a newer reply arrives', () => {
        const props = baseProps({ messages: [user('u1'), reply('a1', '', offer())] });
        const { rerender } = render(<ChatView {...props} />);
        expect(cards()).toHaveLength(1);
        rerender(<ChatView {...props} messages={[...props.messages, user('u2', 'Search the web for: zephyr release date'), reply('a2', 'It shipped in 2021.')]} />);
        expect(cards()).toHaveLength(0);
        expect(screen.getByText('Offered a web search')).toBeInTheDocument();
    });

    it('"Search the web" sends the visible message and the query as options.web', async () => {
        const sendMessage = vi.fn(async () => ({ sent: true }));
        render(<ChatView {...baseProps({ sendMessage, messages: [user('u1'), reply('a1', '', offer())] })} />);
        fireEvent.click(searchButton());
        expect(sendMessage).toHaveBeenCalledTimes(1);
        expect(sendMessage).toHaveBeenCalledWith('Search the web for: zephyr release date', [], { web: { query: 'zephyr release date' } });
    });

    it('sends the edited query, trimmed', async () => {
        const sendMessage = vi.fn(async () => ({ sent: true }));
        const u = userEvent.setup();
        render(<ChatView {...baseProps({ sendMessage, messages: [user('u1'), reply('a1', 'Not in the files.', offer())] })} />);
        const input = within(card()).getByRole('textbox', { name: 'Web search query' });
        await u.clear(input);
        await u.type(input, '  rhel 9 package list{Enter}');
        expect(sendMessage).toHaveBeenCalledWith('Search the web for: rhel 9 package list', [], { web: { query: 'rhel 9 package list' } });
    });

    it('does not touch the composer draft or its attachments', async () => {
        const sendMessage = vi.fn(async () => ({ sent: true }));
        const setDraft = vi.fn();
        const setPendingAttachments = vi.fn();
        render(<ChatView {...baseProps({ sendMessage, setDraft, setPendingAttachments, draft: 'half typed', messages: [user('u1'), reply('a1', '', offer())] })} />);
        fireEvent.click(searchButton());
        expect(setDraft).not.toHaveBeenCalled();
        expect(setPendingAttachments).not.toHaveBeenCalled();
        expect(sendMessage.mock.calls[0][1]).toEqual([]);
    });

    it('"Not now" hides the card and sends nothing', () => {
        const sendMessage = vi.fn();
        render(<ChatView {...baseProps({ sendMessage, messages: [user('u1'), reply('a1', 'Not in the files.', offer())] })} />);
        fireEvent.click(within(card()).getByRole('button', { name: 'Not now' }));
        expect(cards()).toHaveLength(0);
        expect(sendMessage).not.toHaveBeenCalled();
        expect(screen.getByText('Not in the files.')).toBeInTheDocument();   // the reply stays
    });

    it('"Search the web" puts the cursor in the message box, the card having left the page', () => {
        render(<ChatView {...baseProps({ messages: [user('u1'), reply('a1', '', offer())] })} />);
        fireEvent.click(searchButton());
        expect(screen.getByPlaceholderText(/Ask the model/)).toHaveFocus();
    });

    it('leaves the cursor alone on a phone, where it would raise the keyboard', () => {
        render(<ChatView {...baseProps({ effectiveIsMobile: true, messages: [user('u1'), reply('a1', '', offer())] })} />);
        fireEvent.click(within(card()).getByRole('button', { name: 'Not now' }));
        expect(screen.getByPlaceholderText(/Ask the model/)).not.toHaveFocus();
    });

    it('"Not now" puts the cursor in the message box', () => {
        render(<ChatView {...baseProps({ messages: [user('u1'), reply('a1', '', offer())] })} />);
        fireEvent.click(within(card()).getByRole('button', { name: 'Not now' }));
        expect(screen.getByPlaceholderText(/Ask the model/)).toHaveFocus();
    });

    it('the card comes back after a reload (a fresh view of the same note)', () => {
        const messages = [user('u1'), reply('a1', '', offer())];
        const { unmount } = render(<ChatView {...baseProps({ messages })} />);
        fireEvent.click(within(card()).getByRole('button', { name: 'Not now' }));
        unmount();
        render(<ChatView {...baseProps({ messages })} />);
        expect(card()).toBeInTheDocument();
    });

    it('is disabled while a reply is streaming', () => {
        const sendMessage = vi.fn();
        render(<ChatView {...baseProps({ sendMessage, isStreaming: true, messages: [user('u1'), reply('a1', '', { ...offer(), status: 'streaming' })] })} />);
        expect(searchButton()).toBeDisabled();
        fireEvent.click(searchButton());
        fireEvent.keyDown(within(card()).getByRole('textbox', { name: 'Web search query' }), { key: 'Enter' });
        expect(sendMessage).not.toHaveBeenCalled();
    });

    it('is disabled when the offer has no usable query, until the user types one', async () => {
        const u = userEvent.setup();
        const sendMessage = vi.fn(async () => ({ sent: true }));
        render(<ChatView {...baseProps({ sendMessage, messages: [user('u1'), reply('a1', '', { docContext: { notes: [{ kind: 'web_offer', reason: 'because' }] } })] })} />);
        expect(searchButton()).toBeDisabled();
        await u.type(within(card()).getByRole('textbox', { name: 'Web search query' }), 'zephyr');
        expect(searchButton()).toBeEnabled();
    });

    it('shows the model\'s words as text only, with control characters removed', () => {
        const { container } = render(<ChatView {...baseProps({ messages: [user('u1'), reply('a1', '',
            offer('x\u0000y', '<script>alert(1)</script>\u0007 **b**'))] })} />);
        expect(container.querySelector('script')).toBeNull();
        expect(card()).toHaveTextContent('<script>alert(1)</script> **b**');
        expect(within(card()).getByRole('textbox', { name: 'Web search query' })).toHaveValue('xy');
    });

    it('works in the compact side panel', () => {
        const sendMessage = vi.fn(async () => ({ sent: true }));
        render(<ChatView {...baseProps({ compact: true, sendMessage, messages: [user('u1'), reply('a1', '', offer())] })} />);
        fireEvent.click(searchButton());
        expect(sendMessage).toHaveBeenCalledWith('Search the web for: zephyr release date', [], { web: { query: 'zephyr release date' } });
    });

    it('renders in the dark theme too', () => {
        render(<ChatView {...baseProps({ darkMode: true, messages: [user('u1'), reply('a1', '', offer())] })} />);
        expect(card()).toBeInTheDocument();
    });

    it('shows the card beside the sources the same reply used', () => {
        render(<ChatView {...baseProps({ onOpenCitation: vi.fn(), messages: [user('u1'), reply('a1', 'Partly in the files.', { docContext: { notes: [
            { kind: 'sources', docId: 'd', docName: 'Thesis.pdf', pages: [3], used: true },
            { kind: 'web_offer', query: 'zephyr release date', reason: 'The rest is not in the files.' }] } })] })} />);
        expect(screen.getByRole('button', { name: 'Source: page 3 of Thesis.pdf' })).toBeInTheDocument();
        expect(card()).toBeInTheDocument();
    });
});
