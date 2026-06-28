import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorkspaceLink, WorkspaceImage } from './WorkspaceLink';
import * as ctx from '../lib/WorkspaceContext';

function mockWorkspace(over = {}) {
    vi.spyOn(ctx, 'useWorkspace').mockReturnValue({
        workspace: { hasFile: (p) => ['a.md', 'sub/b.md'].includes(p) },
        currentPath: 'a.md',
        navigate: vi.fn(),
        ...over,
    });
}

describe('WorkspaceLink', () => {
    it('navigates for an in-workspace relative .md link', () => {
        const navigate = vi.fn();
        mockWorkspace({ navigate });
        render(<WorkspaceLink href="./sub/b.md">B</WorkspaceLink>);
        fireEvent.click(screen.getByText('B'));
        expect(navigate).toHaveBeenCalledWith('sub/b.md', null);
    });

    it('renders external links with target=_blank and does not navigate', () => {
        const navigate = vi.fn();
        mockWorkspace({ navigate });
        render(<WorkspaceLink href="https://x.com">X</WorkspaceLink>);
        const a = screen.getByText('X');
        expect(a).toHaveAttribute('target', '_blank');
        fireEvent.click(a);
        expect(navigate).not.toHaveBeenCalled();
    });

    it('in single-file mode (no workspace) behaves like a plain external link', () => {
        vi.spyOn(ctx, 'useWorkspace').mockReturnValue({ workspace: null });
        render(<WorkspaceLink href="./b.md">B</WorkspaceLink>);
        expect(screen.getByText('B')).toHaveAttribute('target', '_blank');
    });

    it('renders a dangerous-scheme link inert (no href)', () => {
        const navigate = vi.fn();
        mockWorkspace({ navigate });
        render(<WorkspaceLink href="javascript:alert(1)">click</WorkspaceLink>);
        const el = screen.getByText('click');
        expect(el).not.toHaveAttribute('href');
        fireEvent.click(el);
        expect(navigate).not.toHaveBeenCalled();
    });
});

describe('WorkspaceImage', () => {
    it('revokes the object URL on unmount', async () => {
        const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fake');
        const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
        vi.spyOn(ctx, 'useWorkspace').mockReturnValue({
            workspace: { hasFile: () => true, readBlob: async () => new Blob(['x']) },
            currentPath: 'a.md',
        });
        const { unmount } = render(<WorkspaceImage src="./img/p.png" alt="p" />);
        // let the readBlob microtask resolve
        await Promise.resolve(); await Promise.resolve();
        unmount();
        expect(createSpy).toHaveBeenCalled();
        expect(revokeSpy).toHaveBeenCalledWith('blob:fake');
        createSpy.mockRestore(); revokeSpy.mockRestore();
    });
});
