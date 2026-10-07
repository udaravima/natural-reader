import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ProjectsTab from './ProjectsTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };

describe('ProjectsTab', () => {
  it('shows a card per project with my role and counts, and opens one', () => {
    const onOpenProject = vi.fn();
    render(<ProjectsTab theme={theme} onOpenProject={onOpenProject} projects={[
      { id: 'p1', name: 'Q3 Audit', description: 'Evidence', my_role: 'contributor', member_count: 4, doc_count: 12 },
    ]} />);
    expect(screen.getByText('Contributor')).toBeTruthy();
    expect(screen.getByText('4 members · 12 documents')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open project Q3 Audit' }));
    expect(onOpenProject).toHaveBeenCalledWith('p1');
  });

  it('says how to start when there are none', () => {
    render(<ProjectsTab theme={theme} onOpenProject={vi.fn()} projects={[]} />);
    expect(screen.getByText('No projects yet. Create one with New project.')).toBeTruthy();
  });
});
