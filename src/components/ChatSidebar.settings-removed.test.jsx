import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatSidebar from './ChatSidebar';

const theme = {
    bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '',
    text: '', textSecondary: '', textMuted: '', hover: '',
};

const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, sidebarOpen: true,
    selectedModel: 'qwen3.5:latest', setSelectedModel: vi.fn(),
    availableModels: ['qwen3.5:latest'], reachable: true, refreshModels: vi.fn(),
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
});
