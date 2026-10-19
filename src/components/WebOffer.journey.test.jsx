import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useState } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatView from './ChatView';
import { useChatEngine } from '../hooks/useChatEngine';
import { postTurn } from '../lib/chatStream';
import { apiFetch } from '../utils/apiFetch';
import { bodyOf, sseText } from '../lib/chatFixtures.testutil';

vi.mock('../lib/chatStream', async (importOriginal) => ({ ...(await importOriginal()), postTurn: vi.fn() }));
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
const store = vi.hoisted(() => ({
    getRecentSessions: vi.fn(async () => []), getSession: vi.fn(async () => null), deleteSession: vi.fn(),
    renameSession: vi.fn(), updateSessionPins: vi.fn(), importLegacy: vi.fn(), isLocalId: vi.fn(() => false),
}));
vi.mock('../lib/sessionStore', () => ({ makeSessionStore: () => store }));

// The journey the person takes, with App's wiring reproduced here: ask a
// question the documents don't answer → the reply is the card → edit the
// query → "Search the web" → the next turn runs that search → the answer
// cites the site. The server's events are written the way orchestrator.py
// emits them in `ask` mode (an offer turn may have no text at all).
const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '', viewportBg: '' };
// The server gives every turn its own message ids.
const start = (n) => ({ type: 'start', messageId: `a-server-${n}`, userMessageId: `u-server-${n}`, sessionId: 's' });
const FINISH = { type: 'finish', finishReason: 'stop', stats: { doneReason: 'stop' } };
const stream = (events) => ({ ok: true, status: 200, body: bodyOf(sseText(events)), clone() { return this; } });

const offerTurn = () => [
    start(1), { type: 'start-step', step: 1 },
    { type: 'tool-input-available', toolCallId: 'c1', toolName: 'offer_web_search', input: { query: 'rhel 9 package list', reason: 'The files do not list packages.' }, round: 1 },
    { type: 'tool-output-available', toolCallId: 'c1', output: { ok: true } },
    { type: 'data-web-offer', query: 'rhel 9 package list', reason: 'The files do not list packages.' },
    { type: 'finish-step', finishReason: 'stop', step: 1 }, FINISH,
];
const answerTurn = (n) => [
    start(n), { type: 'start-step', step: 1 },
    { type: 'text-start', id: 't1' }, { type: 'text-delta', id: 't1', delta: 'RHEL 9.4 ships it [1].' }, { type: 'text-end', id: 't1' },
    { type: 'data-web-sources', pages: [{ n: 1, title: 'RHEL 9 packages', url: 'https://access.example.com/rhel9' }] },
    { type: 'finish-step', finishReason: 'stop', step: 1 }, FINISH,
];

function App() {
    const [draft, setDraft] = useState('');
    const engine = useChatEngine({
        selectedModel: 'ollama:m', chatTtsMode: 'after-complete', chatAutoTts: false, inference: undefined,
        isLocalhost: false, selectedVoice: 'v', playbackSpeed: 1, requestTimeout: 15, apiHost: '', apiPort: '8000',
        currentDocId: null, userId: 'u1',
        synthesizeText: vi.fn(), playChatUrl: vi.fn(), playChatSpeech: vi.fn(), stopChatPlayback: vi.fn(), showToast: vi.fn(),
    });
    return (
        <ChatView theme={theme} darkMode={false} effectiveIsMobile={false} messages={engine.messages}
            isStreaming={engine.isStreaming} selectedModel="ollama:m" reachable sendMessage={engine.sendMessage}
            stopStream={engine.stopStream} speakingMessageId={null} speakMessage={vi.fn()} stopSpeaking={vi.fn()}
            downloadingMessageId={null} showToast={vi.fn()} draft={draft} setDraft={setDraft}
            onOpenCitation={vi.fn()} scope={engine.scope} setScope={engine.setScope} loadProjects={async () => []} />
    );
}

beforeEach(() => {
    vi.resetAllMocks();
    store.getRecentSessions.mockImplementation(async () => []);
    store.getSession.mockImplementation(async () => null);
    store.isLocalId.mockImplementation(() => false);
    apiFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ models: [{ id: 'ollama:m', provider: 'ollama', name: 'm', capabilities: {} }], budget: null }) });
});

describe('the web offer, start to finish (C2.1 Task 18)', () => {
    it('ask → the reply is the card → edit the query → search → the answer cites the site, and the next turn asks again', async () => {
        const user = userEvent.setup();
        postTurn.mockResolvedValueOnce(stream(offerTurn())).mockResolvedValueOnce(stream(answerTurn(2)));
        render(<App />);

        // 1-2. Ask; the model offers before writing anything, so the card is the whole reply.
        await user.type(screen.getByPlaceholderText(/Ask the model/), 'Which RHEL 9 packages ship auditd?{Enter}');
        const card = await screen.findByRole('region', { name: 'Web search offer' });
        expect(postTurn.mock.calls[0][0].body.context).not.toHaveProperty('web');
        expect(card).toHaveTextContent('The files do not list packages.');
        await waitFor(() => expect(within(card).getByRole('button', { name: 'Search the web' })).toBeEnabled());

        // 3-4. Edit the query, then search.
        const input = within(card).getByRole('textbox', { name: 'Web search query' });
        expect(input).toHaveValue('rhel 9 package list');
        await user.clear(input);
        await user.type(input, 'rhel 9 audit packages');
        await user.click(within(card).getByRole('button', { name: 'Search the web' }));

        // 5. The next turn runs exactly that search.
        await waitFor(() => expect(postTurn).toHaveBeenCalledTimes(2));
        const second = postTurn.mock.calls[1][0].body;
        expect(second.message.content).toBe('Search the web for: rhel 9 audit packages');
        expect(second.context.web).toEqual({ query: 'rhel 9 audit packages' });
        expect(screen.getByText('Search the web for: rhel 9 audit packages')).toBeInTheDocument();   // the visible user message

        // 6. The answer cites the site: a web source chip, and the old offer is only a line.
        const chip = await screen.findByRole('link', { name: 'access.example.com' });
        expect(chip).toHaveAttribute('href', 'https://access.example.com/rhel9');
        expect(screen.getByText('RHEL 9.4 ships it [1].')).toBeInTheDocument();
        expect(screen.queryByRole('region', { name: 'Web search offer' })).toBeNull();
        expect(screen.getByText('Offered a web search')).toBeInTheDocument();

        // The next turn asks again: no web in its context.
        postTurn.mockResolvedValueOnce(stream(answerTurn(3)));
        await user.type(screen.getByPlaceholderText(/Ask the model/), 'And for RHEL 8?{Enter}');
        await waitFor(() => expect(postTurn).toHaveBeenCalledTimes(3));
        expect(postTurn.mock.calls[2][0].body.context).not.toHaveProperty('web');
    });

    it('"Not now" asks nothing; the user can carry on with an ordinary question', async () => {
        const user = userEvent.setup();
        postTurn.mockResolvedValueOnce(stream(offerTurn()));
        render(<App />);
        await user.type(screen.getByPlaceholderText(/Ask the model/), 'Which RHEL 9 packages ship auditd?{Enter}');
        const card = await screen.findByRole('region', { name: 'Web search offer' });
        await user.click(within(card).getByRole('button', { name: 'Not now' }));
        expect(screen.queryByRole('region', { name: 'Web search offer' })).toBeNull();
        expect(postTurn).toHaveBeenCalledTimes(1);
        expect(screen.getByPlaceholderText(/Ask the model/)).toHaveFocus();
    });
});
