import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatSidebar from './ChatSidebar';

const theme = {
    bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '',
    text: '', textSecondary: '', textMuted: '', hover: '',
};

const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, sidebarOpen: true,
    selectedModel: 'ollama:qwen3.5:latest', setSelectedModel: vi.fn(),
    availableModels: [{ id: 'ollama:qwen3.5:latest', provider: 'ollama', kind: 'ollama', name: 'qwen3.5:latest', capabilities: {} }],
    reachable: true, refreshModels: vi.fn(),
    inferenceBudget: null,
    messages: [], clearHistory: vi.fn(),
    sessions: [], activeSessionId: null, events: [],
    newSession: vi.fn(), switchToSession: vi.fn(),
    deleteSession: vi.fn(), renameSession: vi.fn(),
    ...over,
});

describe('ChatSidebar — settings moved to the Settings page', () => {
    it('no longer renders the inference / read-aloud / source controls', () => {
        render(<ChatSidebar {...baseProps()} />);
        expect(screen.queryByText(/read-aloud mode/i)).toBeNull();
        expect(screen.queryByText(/context window/i)).toBeNull();
        expect(screen.queryByText(/inference source/i)).toBeNull();
    });

    it('still renders the model picker and sessions', () => {
        render(<ChatSidebar {...baseProps()} />);
        expect(screen.getByText('MODEL')).toBeInTheDocument();
        expect(screen.getByText('Sessions')).toBeInTheDocument();
    });

    it('shows a disabled "(unavailable)" option when the saved model has no matching option, so the select never silently shows a different model than what will be sent', () => {
        const b = baseProps({
            selectedModel: 'local:qwen3.5:latest',
            availableModels: [{ id: 'local:llama3.2:3b', provider: 'local', kind: 'ollama', name: 'llama3.2:3b', capabilities: {} }],
        });
        render(<ChatSidebar {...b} />);
        const select = screen.getByRole('combobox');
        expect(select).toHaveValue('local:qwen3.5:latest');
        expect(screen.getByText('local:qwen3.5:latest (unavailable)')).toBeInTheDocument();
    });
});
