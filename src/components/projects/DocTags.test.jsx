import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import DocTags from './DocTags';

const theme = { bg: '', bgTertiary: '', border: '', textSecondary: '', textMuted: '' };
const doc = (over = {}) => ({ id: 'x', name: 'report.pdf', doc_id: 'h1', state: 'indexed', tags: ['finance', 'risk'],
  description: 'Yearly results.', generated: true, edited: false, describing: false, ...over });

function mount(over = {}, props = {}) {
  const p = { theme, doc: doc(over), canEdit: false, helperOn: true, busy: false, activeTag: null,
    onSave: vi.fn(async () => true), onGenerate: vi.fn(async () => true), onTag: vi.fn(), ...props };
  const r = render(<DocTags {...p} />);
  return { ...p, ...r };
}

describe('DocTags (helper spec §5.4)', () => {
  it('a Reader sees the description and the tags, and no pencil or Generate', () => {
    mount();
    expect(screen.getByText('Yearly results.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Show documents tagged finance' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit tags and description of report.pdf' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Generate tags for report.pdf' })).toBeNull();
  });

  it('a tag chip asks for the list filtered to it, and shows when it is the filter', () => {
    const p = mount({}, { activeTag: 'risk' });
    fireEvent.click(screen.getByRole('button', { name: 'Show documents tagged finance' }));
    expect(p.onTag).toHaveBeenCalledWith('finance');
    expect(screen.getByRole('button', { name: 'Show documents tagged risk' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('a Contributor edits tags and description in place', async () => {
    const p = mount({}, { canEdit: true });
    fireEvent.click(screen.getByRole('button', { name: 'Edit tags and description of report.pdf' }));
    fireEvent.change(screen.getByLabelText('Tags for report.pdf'), { target: { value: 'finance, Q3 , ' } });
    fireEvent.change(screen.getByLabelText('Description of report.pdf'), { target: { value: 'Our words.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(p.onSave).toHaveBeenCalledWith({ tags: ['finance', 'Q3'], description: 'Our words.' });
  });

  it("offers the helper's words back when the project's replace them", () => {
    const p = mount({ edited: true }, { canEdit: true });
    fireEvent.click(screen.getByRole('button', { name: 'Edit tags and description of report.pdf' }));
    fireEvent.click(screen.getByRole('button', { name: "Use the helper's" }));
    expect(p.onSave).toHaveBeenCalledWith({ tags: null, description: null });
  });

  it('Generate runs at once when nothing was typed here', () => {
    const p = mount({}, { canEdit: true });
    fireEvent.click(screen.getByRole('button', { name: 'Generate tags for report.pdf' }));
    expect(p.onGenerate).toHaveBeenCalled();
  });

  it('Generate confirms before replacing hand-typed values', () => {
    const p = mount({ edited: true }, { canEdit: true });
    fireEvent.click(screen.getByRole('button', { name: 'Generate tags for report.pdf' }));
    expect(p.onGenerate).not.toHaveBeenCalled();
    expect(screen.getByText("Replace this project's own tags and description with new ones from the helper?")).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(p.onGenerate).toHaveBeenCalled();
  });

  it('says "Writing tags…" while the helper has it, and offers no Generate then', () => {
    mount({ describing: true, tags: [], description: null, generated: false }, { canEdit: true });
    expect(screen.getByText('Writing tags…')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Generate tags for report.pdf' })).toBeNull();
  });

  it('offers Generate only for an indexed document, with the helper on', () => {
    mount({ state: 'indexing', tags: [], description: null, generated: false }, { canEdit: true });
    expect(screen.queryByRole('button', { name: 'Generate tags for report.pdf' })).toBeNull();
  });

  it('with the helper off a Contributor can still type tags, but gets no Generate', () => {
    mount({ tags: [], description: null, generated: false }, { canEdit: true, helperOn: false });
    expect(screen.getByRole('button', { name: 'Edit tags and description of report.pdf' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Generate tags for report.pdf' })).toBeNull();
  });

  it('renders nothing for a Reader when there is nothing to show', () => {
    const { container } = mount({ tags: [], description: null, generated: false }, { helperOn: false });
    expect(container.textContent).toBe('');
  });
});
