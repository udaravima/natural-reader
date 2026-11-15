import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import BulkUploadPanel from './BulkUploadPanel';

const theme = { bg: '', border: '', textMuted: '', textSecondary: '' };
const project = { id: 'p1', name: 'Network handbook' };
const doc = (name, version) => ({ id: `id-${name}`, name, version });
const MB = 1024 * 1024;

function batch(over = {}) {
  return { phase: 'planned', rows: [], leftOut: 0, hashed: true, progress: null, summary: null, error: null,
    describe: true, pick: vi.fn(), choose: vi.fn(), setDescribe: vi.fn(), start: vi.fn(), stop: vi.fn(), retry: vi.fn(),
    reset: vi.fn(), ...over };
}
const row = (path, action, over = {}) => ({ path, name: path.split('/').pop(), size: MB, action, document: null,
  version: null, candidates: [], code: null, message: null, choice: null, status: null, ...over });

const plan = [
  row('intro.md', 'new'),
  row('setup.pdf', 'version', { document: doc('setup.pdf', 2), version: 3, choice: 'version' }),
  row('glossary.md', 'unchanged', { document: doc('glossary.md', 1), version: 1 }),
  row('old-setup.pdf', 'older_version', { document: doc('setup.pdf', 2), version: 2, choice: 'leave' }),
  row('b/README.md', 'new', { repeat: true, choice: 'skip' }),
  row('notes/README.md', 'ambiguous', { choice: 'skip', candidates: [
    { id: 'c1', name: 'README.md', version: 3, added_by: { name: 'Alice' }, added_at: '2026-11-02T10:00:00Z' },
    { id: 'c2', name: 'README.md', version: 1, added_by: { name: 'Bob' }, added_at: '2026-11-03T10:00:00Z' }] }),
  row('huge-scan.pdf', 'refused', { code: 'too_large', message: 'Over the 50 MB limit for a PDF.' }),
];

describe('BulkUploadPanel', () => {
  it('offers "Write tags and descriptions" ticked when a helper is set up, and passes a change on', () => {
    const b = batch({ rows: [row('intro.md', 'new')] });
    render(<BulkUploadPanel theme={theme} project={project} batch={b} helperOn />);
    const tick = screen.getByRole('checkbox', { name: 'Write tags and descriptions' });
    expect(tick.checked).toBe(true);
    fireEvent.click(tick);
    expect(b.setDescribe).toHaveBeenCalledWith(false);
  });

  it('shows the tick as the batch holds it', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ rows: [row('a.md', 'new')], describe: false })} helperOn />);
    expect(screen.getByRole('checkbox', { name: 'Write tags and descriptions' }).checked).toBe(false);
  });

  it('has no tick when no helper is set up', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ rows: [row('intro.md', 'new')] })} />);
    expect(screen.queryByRole('checkbox', { name: 'Write tags and descriptions' })).toBeNull();
  });

  it('shows each row in plain words, its menu, and the counts', () => {
    const b = batch({ rows: plan });
    render(<BulkUploadPanel theme={theme} project={project} batch={b} />);
    expect(screen.getByText('Upload to "Network handbook"')).toBeTruthy();
    expect(screen.getByText('7 files · 2.0 MB to send')).toBeTruthy();
    for (const text of ['New document', 'New version (v3) of setup.pdf', 'Unchanged: not sent',
      'Same as v2 of setup.pdf: left alone', 'Another file here is named README.md: skipped',
      '2 documents here are named README.md: skipped', 'Over the 50 MB limit for a PDF.']) {
      expect(screen.getByText(text)).toBeTruthy();
    }
    expect(screen.getByText('1 new · 1 new version · 1 unchanged · 1 left alone · 2 skipped · 1 can\'t be sent')).toBeTruthy();
    expect(screen.queryByLabelText('What to do with intro.md')).toBeNull();
    const menu = screen.getByLabelText('What to do with notes/README.md');
    expect(within(menu).getAllByRole('option').map((o) => o.textContent.split(' · ').slice(0, 2).join(' · ')))
      .toEqual(['Skip', 'v3 · added by Alice', 'v1 · added by Bob', 'Separate document']);
    fireEvent.change(screen.getByLabelText('What to do with setup.pdf'), { target: { value: 'separate' } });
    expect(b.choose).toHaveBeenCalledWith(1, 'separate');
    fireEvent.click(screen.getByRole('button', { name: 'Upload 2' }));
    expect(b.start).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(b.reset).toHaveBeenCalled();
  });

  it('says what a changed choice will do', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ rows: [
      row('setup.pdf', 'version', { document: doc('setup.pdf', 2), version: 3, choice: 'separate' }),
      row('old.pdf', 'older_version', { document: doc('setup.pdf', 2), version: 1, choice: 'restore' }),
    ] })} />);
    expect(screen.getByText('New document, beside the existing setup.pdf')).toBeTruthy();
    expect(screen.getByText('Same as v1 of setup.pdf: restored as current')).toBeTruthy();
    expect(screen.getByText(/next time that file will ask which one it belongs to/)).toBeTruthy();
  });

  it('gathers left-out files into one line', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ rows: [row('a.md', 'new')], leftOut: 23 })} />);
    expect(screen.getByText("23 files aren't PDF, Markdown or text: left out")).toBeTruthy();
  });

  it('says when unchanged files cannot be detected', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ rows: [row('a.md', 'new')], hashed: false })} />);
    expect(screen.getByText("Unchanged files can't be detected before sending on this connection.")).toBeTruthy();
  });

  it('says nothing is to be sent when every file is unchanged', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ rows: [
      row('a.md', 'unchanged', { document: doc('a.md', 1), version: 1 })] })} />);
    expect(screen.getByText('1 file · nothing to send')).toBeTruthy();
  });

  it('has nothing to upload when every file was left out', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ leftOut: 2 })} />);
    expect(screen.getByRole('button', { name: 'Upload 0' }).disabled).toBe(true);
  });

  it('shows the reading progress', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ phase: 'reading', progress: { done: 34, total: 180 } })} />);
    expect(screen.getByText('Reading files · 34 of 180')).toBeTruthy();
  });

  it('while sending, shows each row and gathers the waiting ones; Stop stops', () => {
    const b = batch({ phase: 'sending', rows: [
      row('intro.md', 'new', { status: 'done', answer: { result: 'created' } }),
      row('setup.pdf', 'version', { status: 'done', answer: { result: 'version', document: doc('setup.pdf', 3) } }),
      row('routing.pdf', 'new', { status: 'sending', percent: 64 }),
      row('a.md', 'new', { status: 'waiting' }), row('b.md', 'new', { status: 'waiting' }),
      row('glossary.md', 'unchanged'),
    ] });
    render(<BulkUploadPanel theme={theme} project={project} batch={b} />);
    expect(screen.getByText('Uploading · 2 of 5')).toBeTruthy();
    expect(screen.getByText('New version (v3)')).toBeTruthy();
    expect(screen.getByText('Sending (64%)')).toBeTruthy();
    expect(screen.getByText('2 waiting')).toBeTruthy();
    expect(screen.queryByText('glossary.md')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(b.stop).toHaveBeenCalled();
  });

  it('a failed row keeps the server sentence and offers Try again', () => {
    const b = batch({ phase: 'sending', rows: [row('x.pdf', 'new', { status: 'failed', error: 'Unsupported file type.' })] });
    render(<BulkUploadPanel theme={theme} project={project} batch={b} />);
    expect(screen.getByText('Unsupported file type.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try x.pdf again' }));
    expect(b.retry).toHaveBeenCalledWith(0);
  });

  it('the summary line counts what happened and can be dismissed', () => {
    const b = batch({ phase: 'done', rows: [row('a.md', 'new', { status: 'done', answer: { result: 'created' } }),
      row('x.pdf', 'new', { status: 'failed', error: 'Unsupported file type.' })],
    summary: { sent: 12, created: 9, versions: 3, restored: 0, unchanged: 0, notFiled: 0, failed: 1, stopped: 0 } });
    render(<BulkUploadPanel theme={theme} project={project} batch={b} />);
    expect(screen.getByText('12 sent: 9 new, 3 new versions. 1 failed.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try x.pdf again' })).toBeTruthy(); // a failure opens the details
    fireEvent.click(screen.getByRole('button', { name: 'Hide details' }));
    expect(screen.queryByText('Unsupported file type.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(b.reset).toHaveBeenCalled();
  });

  it('shows why the batch stopped', () => {
    render(<BulkUploadPanel theme={theme} project={project} batch={batch({ phase: 'done', error: 'You can no longer add documents to this project.',
      summary: { sent: 1, created: 1, versions: 0, restored: 0, unchanged: 0, notFiled: 0, failed: 0, stopped: 4 } })} />);
    expect(screen.getByRole('alert').textContent).toContain('You can no longer add documents to this project.');
    expect(screen.getByText('1 sent: 1 new. 4 not sent.')).toBeTruthy();
  });

  it('shows nothing when idle, except a refused plan', () => {
    const { container, rerender } = render(<BulkUploadPanel theme={theme} project={project} batch={batch({ phase: 'idle' })} />);
    expect(container.textContent).toBe('');
    rerender(<BulkUploadPanel theme={theme} project={project} batch={batch({ phase: 'idle', error: 'At most 200 files.' })} />);
    expect(screen.getByRole('alert').textContent).toContain('At most 200 files.');
  });
});
