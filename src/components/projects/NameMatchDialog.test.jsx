import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import NameMatchDialog from './NameMatchDialog';

const theme = { bg: '', border: '', textSecondary: '' };
const doc = { id: 'x', name: 'report.pdf', version: 3 };

describe('NameMatchDialog', () => {
  it('offers a new version, keep both, or cancel for a name match', () => {
    const onChoose = vi.fn();
    const onCancel = vi.fn();
    render(<NameMatchDialog theme={theme} projectName="Q3" conflict={{ error: 'name_exists', document: doc }} onChoose={onChoose} onCancel={onCancel} />);
    expect(screen.getByRole('dialog').textContent).toContain('A document called report.pdf is already in Q3 (v3).');
    fireEvent.click(screen.getByRole('button', { name: 'Add as new version' }));
    expect(onChoose).toHaveBeenCalledWith({ asVersionOf: 'x' });
    fireEvent.click(screen.getByRole('button', { name: 'Keep both' }));
    expect(onChoose).toHaveBeenCalledWith({ keepBoth: true });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalled();
  });

  it('offers a restore for an older version', () => {
    const onChoose = vi.fn();
    render(<NameMatchDialog theme={theme} projectName="Q3" conflict={{ error: 'older_version', document: doc, version: 1 }} onChoose={onChoose} onCancel={vi.fn()} />);
    expect(screen.getByRole('dialog').textContent).toContain('This file is an older version (v1) of report.pdf in Q3.');
    fireEvent.click(screen.getByRole('button', { name: 'Restore v1 as current' }));
    expect(onChoose).toHaveBeenCalledWith({ asVersionOf: 'x' });
  });

  it('Escape cancels', () => {
    const onCancel = vi.fn();
    render(<NameMatchDialog theme={theme} projectName="Q3" conflict={{ error: 'name_exists', document: doc }} onChoose={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onCancel).toHaveBeenCalled();
  });

  it('Escape does nothing while the choice is being sent', () => {
    const onCancel = vi.fn();
    render(<NameMatchDialog theme={theme} projectName="Q3" conflict={{ error: 'name_exists', document: doc }} busy onChoose={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onCancel).not.toHaveBeenCalled();
  });
});
