import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within, act, cleanup } from '@testing-library/react';
import ProjectDocsTab from './ProjectDocsTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const can = (over = {}) => ({ edit: false, manage_members: false, manage_owners: false, file_docs: false,
  remove_docs: false, delete: false, leave: true, ...over });
const project = (over = {}) => ({ id: 'p1', name: 'Q3', my_role: 'contributor', can: can({ file_docs: true }), ...over });
const maintainer = () => project({ my_role: 'maintainer', can: can({ file_docs: true, remove_docs: true }) });
const report = { id: 'x', name: 'report.pdf', version: 2, versions_count: 2, doc_id: 'h2', state: 'indexed' };
const final = { id: 'y', name: 'report-final.pdf', version: 1, versions_count: 1, doc_id: 'h3', state: 'indexed' };
const history = [
  { version: 2, doc_id: 'h2', file_name: 'report.pdf', note: 'fixed table 4', origin: 'upload', origin_version: null, is_current: true, added_by: { id: 'u', name: 'Bob' }, added_at: '2026-10-11T00:00:00Z', state: 'indexed' },
  { version: 1, doc_id: 'h1', file_name: 'report.pdf', note: null, origin: 'upload', origin_version: null, is_current: false, added_by: { id: 'u2', name: 'Alice' }, added_at: '2026-09-28T00:00:00Z', state: 'indexed' },
];
const mineFree = { doc_id: 'd2', file_name: 'Mine.pdf', state: 'indexed', projects: [], added_via: 'upload' };

function api(over = {}) {
  return {
    documents: vi.fn(async () => [report, final]),
    versions: vi.fn(async () => history),
    myDocs: vi.fn(async () => [mineFree]),
    fileDoc: vi.fn(async () => ({ filed: { document_id: 'n', version: 1 } })),
    uploadVersion: vi.fn(async () => ({ version: 3, doc_id: 'h9', restored_from: null, state: 'indexing' })),
    versionFromLibrary: vi.fn(async () => ({ version: 3, doc_id: 'd2', restored_from: null, state: 'indexed' })),
    restoreVersion: vi.fn(async () => ({ version: 3, doc_id: 'h1', restored_from: 1, state: 'indexed' })),
    deleteVersion: vi.fn(async () => null),
    mergeDocuments: vi.fn(async () => report),
    removeDocument: vi.fn(async () => null),
    ...over,
  };
}

function mount(p, a = api()) {
  const props = { theme, api: a, project: p, showToast: vi.fn(), onRefused: vi.fn(), onChanged: vi.fn(), onOpenDoc: vi.fn() };
  render(<ProjectDocsTab {...props} />);
  return props;
}

describe('ProjectDocsTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists one row per document with its version count', async () => {
    mount(project());
    expect(await screen.findByText('report.pdf')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'History of report.pdf' }).textContent).toBe('v2 · 2 versions');
    expect(screen.getByRole('button', { name: 'History of report-final.pdf' }).textContent).toBe('v1 · 1 version');
  });

  it('shows a failed load inline with Retry', async () => {
    const a = api({ documents: vi.fn().mockRejectedValueOnce(new Error('Could not load documents.')).mockResolvedValue([report]) });
    mount(project(), a);
    expect((await screen.findByRole('alert')).textContent).toContain('Could not load documents.');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('report.pdf')).toBeTruthy();
  });

  it('expands the history newest first; old lines offer Open, Restore and Delete by role', async () => {
    const props = mount(maintainer());
    fireEvent.click(await screen.findByRole('button', { name: 'History of report.pdf' }));
    const v1 = await screen.findByTestId('version-1');
    const v2 = screen.getByTestId('version-2');
    expect(v2.textContent).toContain('current');
    expect(v2.textContent).toContain('"fixed table 4"');
    expect(within(v2).queryByRole('button', { name: 'Restore v2' })).toBeNull();
    fireEvent.click(within(v1).getByRole('button', { name: 'Open v1' }));
    expect(props.onOpenDoc).toHaveBeenCalledWith({ doc_id: 'h1', file_name: 'report.pdf' });
    expect(within(v1).getByRole('button', { name: 'Restore v1' })).toBeTruthy();
    expect(within(v1).getByRole('button', { name: 'Delete v1' })).toBeTruthy();
  });

  it('a Contributor can restore but not delete or merge', async () => {
    const a = api();
    const props = mount(project(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'History of report.pdf' }));
    const v1 = await screen.findByTestId('version-1');
    expect(within(v1).queryByRole('button', { name: 'Delete v1' })).toBeNull();
    fireEvent.click(within(v1).getByRole('button', { name: 'Restore v1' }));
    await waitFor(() => expect(a.restoreVersion).toHaveBeenCalledWith('p1', 'x', 1));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Restored v1 as v3.', 3000));
    fireEvent.click(screen.getByRole('button', { name: 'More actions for report.pdf' }));
    expect(screen.getByRole('menuitem', { name: 'Upload new version' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Mark as newer version of…' })).toBeNull();
  });

  it('deletes an old version after an inline confirmation', async () => {
    const a = api();
    mount(maintainer(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'History of report.pdf' }));
    fireEvent.click(within(await screen.findByTestId('version-1')).getByRole('button', { name: 'Delete v1' }));
    expect(screen.getByText(/Delete v1 for good\?/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => expect(a.deleteVersion).toHaveBeenCalledWith('p1', 'x', 1));
  });

  it('uploads a new version from a file with a note', async () => {
    const a = api();
    const props = mount(project(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions for report.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Upload new version' }));
    const file = new File(['x'], 'report v3.pdf');
    fireEvent.change(screen.getByLabelText('Choose a file for the new version of report.pdf'), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText('What changed?'), { target: { value: 'new numbers' } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(a.uploadVersion).toHaveBeenCalledWith('p1', 'x', file, 'new numbers'));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('report v3.pdf is now v3 (indexing…).', 3000));
  });

  it('uploads a new version from my library', async () => {
    const a = api();
    mount(project(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions for report.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Upload new version' }));
    fireEvent.change(screen.getByLabelText('New version from my library'), { target: { value: 'd2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(a.versionFromLibrary).toHaveBeenCalledWith('p1', 'x', 'd2', ''));
  });

  it('says so when an uploaded file matched an older version', async () => {
    const a = api({ uploadVersion: vi.fn(async () => ({ version: 3, doc_id: 'h1', restored_from: 1, state: 'indexed' })) });
    const props = mount(project(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions for report.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Upload new version' }));
    fireEvent.change(screen.getByLabelText('Choose a file for the new version of report.pdf'), { target: { files: [new File(['x'], 'old.pdf')] } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('That file matched v1, so it was restored as v3.', 3000));
  });

  it('merges after a confirmation that names the result', async () => {
    const a = api();
    mount(maintainer(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions for report-final.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark as newer version of…' }));
    fireEvent.change(screen.getByLabelText('Mark report-final.pdf as a newer version of'), { target: { value: 'x' } });
    expect(screen.getByText(/report-final\.pdf becomes v3 of report\.pdf, and the row will be called report-final\.pdf\. This can't be undone/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm merge' }));
    await waitFor(() => expect(a.mergeDocuments).toHaveBeenCalledWith('p1', 'x', 'y'));
  });

  it("refreshes an open history when another row's merge adds a version to it", async () => {
    const merged = { ...report, version: 3, versions_count: 3 };
    const v3 = { ...history[0], version: 3, doc_id: 'h3', file_name: 'report-final.pdf', origin: 'merge', origin_version: 1 };
    const a = api({
      documents: vi.fn().mockResolvedValueOnce([report, final]).mockResolvedValue([merged]),
      versions: vi.fn().mockResolvedValueOnce(history)
        .mockResolvedValue([v3, { ...history[0], is_current: false }, history[1]]),
    });
    mount(maintainer(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'History of report.pdf' }));
    await screen.findByTestId('version-2');
    expect(screen.queryByTestId('version-3')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More actions for report-final.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark as newer version of…' }));
    fireEvent.change(screen.getByLabelText('Mark report-final.pdf as a newer version of'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm merge' }));
    expect(await screen.findByTestId('version-3')).toBeTruthy();
    expect(a.versions).toHaveBeenCalledTimes(2);
    expect(within(screen.getByTestId('version-3')).getByText('current')).toBeTruthy();
  });

  it('a double tap on Confirm delete sends one request', async () => {
    let finish;
    const a = api({ deleteVersion: vi.fn(() => new Promise((r) => { finish = r; })) });
    mount(maintainer(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'History of report.pdf' }));
    fireEvent.click(within(await screen.findByTestId('version-1')).getByRole('button', { name: 'Delete v1' }));
    const confirm = screen.getByRole('button', { name: 'Confirm delete' });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(a.deleteVersion).toHaveBeenCalledTimes(1);
    await act(async () => finish(null));
  });

  it('a double tap on Confirm remove sends one request', async () => {
    let finish;
    const a = api({ removeDocument: vi.fn(() => new Promise((r) => { finish = r; })) });
    mount(maintainer(), a);
    fireEvent.click(await screen.findByLabelText('Remove report.pdf from Q3'));
    const confirm = screen.getByRole('button', { name: 'Confirm remove' });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(a.removeDocument).toHaveBeenCalledTimes(1);
    await act(async () => finish(null));
  });

  it('asks before removing, naming the versions, then removes', async () => {
    const a = api();
    mount(maintainer(), a);
    fireEvent.click(await screen.findByLabelText('Remove report.pdf from Q3'));
    expect(screen.getByText(/Remove report\.pdf and its 2 versions from Q3\? If nobody else has it in their library, it is deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }));
    await waitFor(() => expect(a.removeDocument).toHaveBeenCalledWith('p1', 'x'));
  });

  it('files one of my uploads through the name-match flow', async () => {
    const conflict = { error: 'name_exists', document: { id: 'x', name: 'report.pdf', version: 2 } };
    const a = api({ fileDoc: vi.fn().mockResolvedValueOnce({ conflict }).mockResolvedValueOnce({ filed: { document_id: 'x', version: 3 } }) });
    mount(project(), a);
    const select = await screen.findByLabelText('File a document into this project');
    fireEvent.change(select, { target: { value: 'd2' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Add as new version' }));
    await waitFor(() => expect(a.fileDoc).toHaveBeenLastCalledWith('p1', 'd2', { asVersionOf: 'x' }));
  });

  it('has no file, upload or remove controls for a Reader', async () => {
    mount(project({ my_role: 'reader', can: can() }));
    await screen.findByText('report.pdf');
    expect(screen.queryByLabelText('File a document into this project')).toBeNull();
    expect(screen.queryByRole('button', { name: 'More actions for report.pdf' })).toBeNull();
    expect(screen.queryByLabelText('Remove report.pdf from Q3')).toBeNull();
  });

  it('shows the notice and asks the page to reload when the server refuses', async () => {
    const a = api({ removeDocument: vi.fn(async () => { throw new Error('Only Maintainers or Owners can do that.'); }) });
    const props = mount(maintainer(), a);
    fireEvent.click(await screen.findByLabelText('Remove report.pdf from Q3'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Only Maintainers or Owners can do that.', 5000));
    expect(props.onRefused).toHaveBeenCalled();
  });

  it('admin non-member sees the members-only note instead of documents', async () => {
    const a = api();
    mount(project({ my_role: null, can: can() }), a);
    expect(await screen.findByText("Only members can see this project's documents.")).toBeTruthy();
    expect(a.documents).not.toHaveBeenCalled();
  });
});

describe('ProjectDocsTab while content is indexing', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); });
  afterEach(() => vi.useRealTimers());

  const badge = () => screen.getByText('report.pdf').nextSibling.textContent;
  const flush = () => act(async () => {});
  const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

  it('re-reads the list every 3 s until every row has settled, then stops', async () => {
    const documents = vi.fn()
      .mockResolvedValueOnce([{ ...report, state: 'stored' }, final])
      .mockResolvedValueOnce([{ ...report, state: 'indexing' }, final])
      .mockResolvedValue([report, final]);
    mount(project(), api({ documents }));
    await flush();
    expect(badge()).toBe('stored');
    await advance(2999);
    expect(documents).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(documents).toHaveBeenCalledTimes(2);
    expect(badge()).toBe('indexing');
    await advance(3000);
    expect(documents).toHaveBeenCalledTimes(3);
    expect(badge()).toBe('indexed');
    await advance(30000);
    expect(documents).toHaveBeenCalledTimes(3);
  });

  it('never starts a read while the previous one is still out', async () => {
    let finish;
    const documents = vi.fn()
      .mockResolvedValueOnce([{ ...report, state: 'extracting' }])
      .mockImplementationOnce(() => new Promise((r) => { finish = r; }))
      .mockResolvedValue([report]);
    mount(project(), api({ documents }));
    await flush();
    await advance(3000);
    expect(documents).toHaveBeenCalledTimes(2);
    await advance(12000);
    expect(documents).toHaveBeenCalledTimes(2);
    await act(async () => finish([{ ...report, state: 'indexing' }]));
    await advance(3000);
    expect(documents).toHaveBeenCalledTimes(3);
    expect(badge()).toBe('indexed');
  });

  it('stops when the tab unmounts', async () => {
    const documents = vi.fn(async () => [{ ...report, state: 'extracting' }]);
    mount(project(), api({ documents }));
    await flush();
    await advance(3000);
    expect(documents).toHaveBeenCalledTimes(2);
    cleanup();
    await advance(30000);
    expect(documents).toHaveBeenCalledTimes(2);
  });
});
