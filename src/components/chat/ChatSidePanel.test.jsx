import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatSidePanel from './ChatSidePanel';

const theme = { border: '', bgSecondary: '', bgTertiary: '', text: '', textSecondary: '', hover: '' };

describe('ChatSidePanel', () => {
  it('holds the chat, its model picker, and New / Open in Chat / Close', () => {
    const on = { onNewChat: vi.fn(), onExpand: vi.fn(), onClose: vi.fn(), setSelectedModel: vi.fn() };
    render(
      <ChatSidePanel theme={theme} title="My chat" selectedModel="ollama:a"
        availableModels={[{ id: 'ollama:a', provider: 'ollama', name: 'a' }, { id: 'ollama:b', provider: 'ollama', name: 'b' }]}
        {...on}>
        <p>the chat</p>
      </ChatSidePanel>,
    );
    expect(screen.getByRole('complementary', { name: 'Chat beside the page' })).toBeInTheDocument();
    expect(screen.getByText('the chat')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'ollama:b' } });
    expect(on.setSelectedModel).toHaveBeenCalledWith('ollama:b');
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open in the Chat view' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close the chat' }));
    expect([on.onNewChat, on.onExpand, on.onClose].map((f) => f.mock.calls.length)).toEqual([1, 1, 1]);
  });
});
