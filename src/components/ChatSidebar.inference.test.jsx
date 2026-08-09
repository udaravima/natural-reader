import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatSidebar from './ChatSidebar';
import { INFERENCE_DEFAULTS } from '../hooks/inference';

// The "Settings" section (which houses Inference) collapses by default once a
// model is selected (ChatSidebar.jsx: `defaultOpen={!selectedModel}`, a
// pre-existing, out-of-scope behavior). baseProps sets selectedModel, so every
// test that reaches into the Inference controls must expand the section first.
const openSettings = () => fireEvent.click(screen.getByText('Settings'));

const theme = {
    bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '',
    text: '', textSecondary: '', textMuted: '', hover: '',
};

const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, sidebarOpen: true,
    ollamaHost: 'localhost', setOllamaHost: vi.fn(),
    ollamaPort: '11434', setOllamaPort: vi.fn(),
    selectedModel: 'qwen3.5:latest', setSelectedModel: vi.fn(),
    availableModels: ['qwen3.5:latest'], reachable: true, refreshModels: vi.fn(),
    chatTtsMode: 'streaming', setChatTtsMode: vi.fn(),
    chatAutoTts: true, setChatAutoTts: vi.fn(),
    inference: INFERENCE_DEFAULTS, setInference: vi.fn(),
    messages: [], clearHistory: vi.fn(),
    sessions: [], activeSessionId: null, events: [],
    newSession: vi.fn(), switchToSession: vi.fn(),
    deleteSession: vi.fn(), renameSession: vi.fn(),
    ...over,
});

describe('ChatSidebar inference controls', () => {
    it('renders the four inference controls', () => {
        render(<ChatSidebar {...baseProps()} />);
        openSettings();
        expect(screen.getByLabelText('Context window')).toBeInTheDocument();
        expect(screen.getByLabelText('Keep model warm')).toBeInTheDocument();
        expect(screen.getByLabelText('Thinking')).toBeInTheDocument();
        expect(screen.getByLabelText('Max reply tokens')).toBeInTheDocument();
    });

    it('shows Auto as the selected context window when unset', () => {
        render(<ChatSidebar {...baseProps()} />);
        openSettings();
        expect(screen.getByLabelText('Context window')).toHaveValue('auto');
    });

    it('sends a numeric num_ctx when a size is chosen', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ setInference })} />);
        openSettings();
        fireEvent.change(screen.getByLabelText('Context window'), { target: { value: '16384' } });
        expect(setInference).toHaveBeenCalledWith({ numCtx: 16384 });
    });

    it('clears num_ctx back to null when Auto is chosen', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ inference: { ...INFERENCE_DEFAULTS, numCtx: 16384 }, setInference })} />);
        openSettings();
        fireEvent.change(screen.getByLabelText('Context window'), { target: { value: 'auto' } });
        expect(setInference).toHaveBeenCalledWith({ numCtx: null });
    });

    it('stores keep_alive Always as -1', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ setInference })} />);
        openSettings();
        fireEvent.change(screen.getByLabelText('Keep model warm'), { target: { value: '-1' } });
        expect(setInference).toHaveBeenCalledWith({ keepAlive: -1 });
    });

    it('stores keep_alive durations as strings', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ setInference })} />);
        openSettings();
        fireEvent.change(screen.getByLabelText('Keep model warm'), { target: { value: '30m' } });
        expect(setInference).toHaveBeenCalledWith({ keepAlive: '30m' });
    });

    it('stores the thinking level as a string', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ setInference })} />);
        openSettings();
        fireEvent.change(screen.getByLabelText('Thinking'), { target: { value: 'low' } });
        expect(setInference).toHaveBeenCalledWith({ think: 'low' });
    });

    it('no longer renders the legacy Enable thinking toggle', () => {
        render(<ChatSidebar {...baseProps()} />);
        expect(screen.queryByText('Enable thinking')).not.toBeInTheDocument();
    });
});
