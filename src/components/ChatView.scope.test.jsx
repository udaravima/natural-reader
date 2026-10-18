import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatView from './ChatView';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const props = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, messages: [], isStreaming: false,
    selectedModel: 'ollama:m', reachable: true, sendMessage: vi.fn(), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(), downloadingMessageId: null,
    downloadMessageAudio: vi.fn(), showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null,
    scope: { kind: 'none' }, setScope: vi.fn(), loadProjects: vi.fn(async () => []), ...over,
});
const chip = () => screen.getByRole('button', { name: /No document|This document|Project/ });

describe('ChatView — the scope chip (C2.1 Task 17; was "Use this document", v2.4 Task E)', () => {
    it('shows the chip with no document open, and no switch', () => {
        render(<ChatView {...props()} />);
        expect(chip().textContent).toBe('No document');
        expect(screen.queryByRole('switch')).toBeNull();
    });

    it('shows the open document in the chip when it is the scope', () => {
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', scope: { kind: 'document' } })} />);
        expect(chip().textContent).toBe('This document: Thesis.pdf');
    });

    it('shows a project scope', () => {
        render(<ChatView {...props({ scope: { kind: 'project', project: { id: 'p1', name: 'Q3 Audit' } } })} />);
        expect(chip().textContent).toBe('Project: Q3 Audit');
    });

    it('off means no document: the chip says so, and the document stays one click away', () => {
        const setScope = vi.fn();
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', scope: { kind: 'document' }, setScope })} />);
        fireEvent.click(chip());
        fireEvent.click(screen.getByRole('menuitem', { name: 'No document' }));
        expect(setScope).toHaveBeenCalledWith({ kind: 'none' });
    });

    it('with the document not used, the chip says "No document" while the menu still offers it', () => {
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', scope: { kind: 'none' } })} />);
        expect(chip().textContent).toBe('No document');
        fireEvent.click(chip());
        expect(screen.getByRole('menuitem', { name: 'This document: Thesis.pdf' })).toBeInTheDocument();
    });

    it('turns the document back on from the menu', () => {
        const setScope = vi.fn();
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', scope: { kind: 'none' }, setScope })} />);
        fireEvent.click(chip());
        fireEvent.click(screen.getByRole('menuitem', { name: 'This document: Thesis.pdf' }));
        expect(setScope).toHaveBeenCalledWith({ kind: 'document' });
    });

    it('offers the open document\'s projects', () => {
        const setScope = vi.fn();
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', openDocProjects: [{ id: 'p1', name: 'Q3 Audit' }], setScope })} />);
        fireEvent.click(chip());
        fireEvent.click(screen.getByRole('menuitem', { name: 'Q3 Audit' }));
        expect(setScope).toHaveBeenCalledWith({ kind: 'project', project: { id: 'p1', name: 'Q3 Audit' } });
    });

    it('labels an old version of the open document', () => {
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', openDocVersion: 2, scope: { kind: 'document' } })} />);
        expect(chip().textContent).toContain('· v2 (old version)');
    });

    it('has no version label for a current document', () => {
        render(<ChatView {...props({ openDocName: 'Thesis.pdf', scope: { kind: 'document' } })} />);
        expect(chip().textContent).not.toContain('old version');
    });

    it('still renders in isolation, without a scope controller', () => {
        render(<ChatView {...props({ scope: undefined, setScope: undefined, loadProjects: undefined })} />);
        expect(screen.queryByRole('button', { name: /No document|Project/ })).toBeNull();
    });
});
