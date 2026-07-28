import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { WorkspaceProvider, useWorkspace } from './WorkspaceContext';

const fakeWs = { rootName: 'v', hasFile: () => true };

function Probe() {
    const { currentPath, navigate, goBack, goForward, canGoBack, canGoForward } = useWorkspace();
    return (
        <div>
            <span data-testid="path">{currentPath}</span>
            <span data-testid="back">{String(canGoBack)}</span>
            <span data-testid="fwd">{String(canGoForward)}</span>
            <button onClick={() => navigate('b.md')}>nav-b</button>
            <button onClick={() => navigate('c.md')}>nav-c</button>
            <button onClick={goBack}>back</button>
            <button onClick={goForward}>fwd</button>
        </div>
    );
}

describe('WorkspaceProvider', () => {
    it('opens the initial path and tracks Back/Forward history', () => {
        const onOpenDoc = vi.fn();
        render(
            <WorkspaceProvider workspace={fakeWs} initialPath="a.md" onOpenDoc={onOpenDoc}>
                <Probe />
            </WorkspaceProvider>,
        );
        expect(onOpenDoc).toHaveBeenCalledWith('a.md', { anchor: null });
        expect(screen.getByTestId('path').textContent).toBe('a.md');
        expect(screen.getByTestId('back').textContent).toBe('false');

        act(() => { screen.getByText('nav-b').click(); });
        expect(screen.getByTestId('path').textContent).toBe('b.md');
        expect(screen.getByTestId('back').textContent).toBe('true');

        act(() => { screen.getByText('back').click(); });
        expect(screen.getByTestId('path').textContent).toBe('a.md');
        expect(screen.getByTestId('fwd').textContent).toBe('true');

        // Navigating after going back truncates forward history.
        act(() => { screen.getByText('nav-c').click(); });
        expect(screen.getByTestId('path').textContent).toBe('c.md');
        expect(screen.getByTestId('fwd').textContent).toBe('false');
    });

    it('two synchronous navigates in one tick land on the second path with correct history', () => {
        render(
            <WorkspaceProvider workspace={fakeWs} initialPath="a.md" onOpenDoc={vi.fn()}>
                <Probe />
            </WorkspaceProvider>,
        );
        // Dispatch nav-b then nav-c inside a single act — both run synchronously before React flushes.
        act(() => {
            screen.getByText('nav-b').click();
            screen.getByText('nav-c').click();
        });
        expect(screen.getByTestId('path').textContent).toBe('c.md');
        expect(screen.getByTestId('back').textContent).toBe('true');
        // No forward history should exist after two forward navigations from a.md.
        expect(screen.getByTestId('fwd').textContent).toBe('false');
    });

    it('returns workspace:null without a provider', () => {
        function Bare() {
            const { workspace } = useWorkspace();
            return <span>{String(workspace)}</span>;
        }
        render(<Bare />);
        expect(screen.getByText('null')).toBeInTheDocument();
    });
});
