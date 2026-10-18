import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import ChatSidebar from './ChatSidebar';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const session = (id, over = {}) => ({ id, title: `Chat ${id}`, source: 'pg', messageCount: 1, updatedAt: Date.now(), scopeProject: null, ...over });

function mount(sessions) {
    render(<ChatSidebar theme={theme} darkMode={false} effectiveIsMobile={false} sidebarOpen
        selectedModel="" setSelectedModel={vi.fn()} availableModels={[]} inferenceBudget={null} reachable
        refreshModels={vi.fn()} messages={[]} clearHistory={vi.fn()} sessions={sessions} activeSessionId={null}
        events={[]} newSession={vi.fn()} switchToSession={vi.fn()} deleteSession={vi.fn()} renameSession={vi.fn()} />);
}
const rowOf = (title) => screen.getByText(title).closest('div[class*="group"]');

describe('ChatSidebar — project label (C2.1 Task 17)', () => {
    it('labels a project chat with the project name, and a plain chat with nothing', () => {
        mount([session('a', { scopeProject: { id: 'p1', name: 'Q3 Audit' } }), session('b')]);
        expect(within(rowOf('Chat a')).getByText('Q3 Audit')).toBeInTheDocument();
        expect(within(rowOf('Chat b')).queryByText(/Project|Q3/)).toBeNull();
    });

    it('says "Project unavailable" when the reader can no longer see the project', () => {
        mount([session('a', { scopeProject: { id: 'p1', name: null } })]);
        expect(within(rowOf('Chat a')).getByText('Project unavailable')).toBeInTheDocument();
    });

    it('renders the project name as text, never as HTML', () => {
        mount([session('a', { scopeProject: { id: 'p1', name: '<img src=x onerror=alert(1)>' } })]);
        expect(within(rowOf('Chat a')).getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
        expect(document.querySelector('img')).toBeNull();
    });
});
