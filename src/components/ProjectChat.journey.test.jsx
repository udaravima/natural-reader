import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LibraryPage from './library/LibraryPage';
import ChatView from './ChatView';
import ChatSidebar from './ChatSidebar';
import { useChatEngine } from '../hooks/useChatEngine';
import { projectsApi } from '../lib/projectsApi';
import { postTurn } from '../lib/chatStream';
import { apiFetch } from '../utils/apiFetch';
import { bodyOf, loadFixture, sseText } from '../lib/chatFixtures.testutil';

vi.mock('../lib/chatStream', async (importOriginal) => ({ ...(await importOriginal()), postTurn: vi.fn() }));
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
const store = vi.hoisted(() => ({
    getRecentSessions: vi.fn(async () => []), getSession: vi.fn(async () => null), deleteSession: vi.fn(),
    renameSession: vi.fn(), updateSessionPins: vi.fn(), importLegacy: vi.fn(), isLocalId: vi.fn(() => false),
}));
vi.mock('../lib/sessionStore', () => ({ makeSessionStore: () => store }));

// The journey the person takes, with App's wiring (App.jsx handleAskProject,
// chatViewProps) reproduced here: Library → a project → "Ask about this
// project" → the Chat view, whose chip reads the project → send.
const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '', viewportBg: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
const DOC = 'a'.repeat(64);
const can = { edit: false, manage_members: false, manage_owners: false, file_docs: true, remove_docs: false, delete: false, leave: true };
const q3 = { id: 'p1', name: 'Q3 Audit', description: null, my_role: 'reader', member_count: 2, doc_count: 0, can };

function App({ withDoc = true }) {
    const [view, setView] = useState('library');
    const [draft, setDraft] = useState('');
    const api = projectsApi('', '');
    const engine = useChatEngine({
        selectedModel: 'ollama:m', chatTtsMode: 'after-complete', chatAutoTts: false, inference: undefined,
        isLocalhost: false, selectedVoice: 'v', playbackSpeed: 1, requestTimeout: 15, apiHost: '', apiPort: '8000',
        currentDocId: withDoc ? DOC : null, userId: 'u1',
        synthesizeText: vi.fn(), playChatUrl: vi.fn(), playChatSpeech: vi.fn(), stopChatPlayback: vi.fn(), showToast: vi.fn(),
    });
    const askProject = (project) => { engine.startProjectChat(project); setView('chat'); };
    return view === 'library' ? (
        <LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} currentUserId="me" onAskProject={askProject} />
    ) : (
        <>
            <ChatSidebar theme={theme} darkMode={false} effectiveIsMobile={false} sidebarOpen selectedModel="ollama:m"
                setSelectedModel={vi.fn()} availableModels={[]} inferenceBudget={null} reachable refreshModels={vi.fn()}
                messages={engine.messages} clearHistory={vi.fn()} sessions={engine.sessions}
                activeSessionId={engine.activeSessionId} events={engine.events} newSession={engine.newSession}
                switchToSession={engine.switchToSession} deleteSession={vi.fn()} renameSession={vi.fn()} />
            <ChatView theme={theme} darkMode={false} effectiveIsMobile={false} messages={engine.messages}
                isStreaming={engine.isStreaming} selectedModel="ollama:m" reachable sendMessage={engine.sendMessage}
                stopStream={engine.stopStream} speakingMessageId={null} speakMessage={vi.fn()} stopSpeaking={vi.fn()}
                downloadingMessageId={null} showToast={vi.fn()} draft={draft} setDraft={setDraft}
                openDocName={withDoc ? 'Thesis.pdf' : null} openDocProjects={[]} scope={engine.scope}
                setScope={engine.setScope} loadProjects={api.list} />
        </>
    );
}

beforeEach(() => {
    vi.resetAllMocks();
    store.getRecentSessions.mockImplementation(async () => []);
    store.getSession.mockImplementation(async () => null);
    store.isLocalId.mockImplementation(() => false);
    apiFetch.mockImplementation(async (h, p, path) => {
        if (path === '/v1/inference/models') return json(200, { models: [{ id: 'ollama:m', provider: 'ollama', name: 'm', capabilities: {} }], budget: null });
        if (path === '/v1/projects') return json(200, [q3]);
        if (path === '/v1/projects/p1') return json(200, q3);
        if (path.startsWith('/v1/docs') || path.endsWith('/documents')) return json(200, []);
        return json(404, {});
    });
    postTurn.mockImplementation(async () => ({ ok: true, status: 200, body: bodyOf(sseText(loadFixture('plain'))), clone() { return this; } }));
});

describe('project chat, start to finish (C2.1 Task 17)', () => {
    it('Library → project → "Ask about this project" → a chat about it → send', async () => {
        const user = userEvent.setup();
        // Once a turn has been posted, the server's list shows the chat with its project.
        store.getRecentSessions.mockImplementation(async () => (postTurn.mock.calls.length
            ? [{ id: postTurn.mock.calls[0][0].sessionId, title: 'What does the audit say?', source: 'pg', messageCount: 2,
                updatedAt: Date.now(), scopeProject: { id: 'p1', name: 'Q3 Audit' } }]
            : []));
        render(<App />);
        fireEvent.click(await screen.findByRole('tab', { name: 'Projects' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Open project Q3 Audit' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Ask about this project' }));

        // The Chat view opens on a new chat whose chip names the project.
        const chip = await screen.findByRole('button', { name: 'Project: Q3 Audit' });
        expect(chip).toBeInTheDocument();
        await user.type(screen.getByRole('textbox'), 'What does the audit say?{Enter}');
        await waitFor(() => expect(postTurn).toHaveBeenCalledTimes(1));
        const { body } = postTurn.mock.calls[0][0];
        expect(body.message.content).toBe('What does the audit say?');
        expect(body.context).toMatchObject({ project_id: 'p1', doc_id: DOC });

        // The server stores the scope; the sidebar then labels the chat with it.
        const sidebar = screen.getByRole('complementary');
        await waitFor(() => expect(within(sidebar).getAllByText('What does the audit say?').length).toBeGreaterThan(0));
        const row = within(sidebar).getAllByText('What does the audit say?')
            .map((el) => el.closest('div[class*="group"]')).find(Boolean);
        expect(within(row).getByText('Q3 Audit')).toBeInTheDocument();

        // Leaving the project from the chip sends project_id null on the next turn.
        fireEvent.click(screen.getByRole('button', { name: 'Project: Q3 Audit' }));
        fireEvent.click(screen.getByRole('menuitemradio', { name: 'No document' }));
        expect(screen.getByRole('button', { name: 'No document' })).toBeInTheDocument();
        await user.type(screen.getByRole('textbox'), 'And in general?{Enter}');
        await waitFor(() => expect(postTurn).toHaveBeenCalledTimes(2));
        expect(postTurn.mock.calls[1][0].body.context).toMatchObject({ project_id: null, doc_id: null });
    });

    it('a chat with no document open can still pick a project from the chip', async () => {
        const user = userEvent.setup();
        render(<App withDoc={false} />);
        fireEvent.click(await screen.findByRole('tab', { name: 'Projects' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Open project Q3 Audit' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Ask about this project' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Project: Q3 Audit' }));
        fireEvent.click(screen.getByRole('menuitemradio', { name: 'No document' }));
        fireEvent.click(screen.getByRole('button', { name: 'No document' }));
        fireEvent.click(screen.getByRole('menuitem', { name: 'Choose a project…' }));
        fireEvent.click(await screen.findByRole('menuitemradio', { name: 'Q3 Audit' }));
        await user.type(screen.getByRole('textbox'), 'Hello{Enter}');
        await waitFor(() => expect(postTurn).toHaveBeenCalledTimes(1));
        expect(postTurn.mock.calls[0][0].body.context).toMatchObject({ project_id: 'p1', doc_id: null });
    });
});
