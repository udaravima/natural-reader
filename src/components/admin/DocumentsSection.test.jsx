import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { DocumentsSection } from './DocumentsSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const ALL = ['fast', 'standard', 'accurate'];
const row = (env, value, fallback = value, over = {}) => ({
  value, source: 'default', env, fallback, fallback_source: 'default', ...over,
});
const settings = (over = {}) => ({
  convert_by: row('DOCLING_CONVERT_BY', 'readers'),
  presets: row('DOCLING_PRESETS', ALL),
  ocr: row('DOCLING_ALLOW_OCR', true),
  picture_description: row('DOCLING_ALLOW_PICTURE_DESCRIPTION', true),
  max_pages: row('DOCLING_MAX_PAGES', 0),
  ...over,
});
const INFO = [
  { id: 'docling', label: 'Docling', value: 'On', env: 'DOCLING_ENABLED', source: 'env' },
  { id: 'pdf_upload_max', label: 'Largest PDF upload', value: '50 MB', env: 'PDF_UPLOAD_MAX_MB', source: 'default' },
  { id: 'text_upload_max', label: 'Largest text upload', value: '10 MB', env: 'TEXT_UPLOAD_MAX_MB', source: 'default' },
];
const view = (over = {}, info = INFO) => ({ settings: settings(over), info });

function mount({ initial = view(), put } = {}) {
  const onDirtyChange = vi.fn();
  const showToast = vi.fn();
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (path !== '/v1/admin/settings/documents') return json(404, {});
    if (opts?.method === 'PUT') return put ? put(opts) : json(200, initial);
    return json(200, initial);
  });
  render(<DocumentsSection theme={theme} apiHost="" apiPort="" showToast={showToast} onDirtyChange={onDirtyChange} />);
  return { onDirtyChange, showToast };
}

const puts = () => apiFetch.mock.calls.filter(([, , , o]) => o?.method === 'PUT');
const save = () => screen.getByRole('button', { name: 'Save' });
const text = (id) => document.getElementById(id).textContent;

describe('DocumentsSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('saves the limits the admin changed', async () => {
    mount({ put: () => json(200, view({ ocr: row('DOCLING_ALLOW_OCR', false, true, { source: 'admin' }) })) });
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Allow OCR' }));
    fireEvent.change(screen.getByLabelText('Largest conversion'), { target: { value: '200' } });
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ changes: { ocr: false, max_pages: 200 } });
  });

  it('shows the help lines and ties help and source to the controls', async () => {
    mount();
    await screen.findByRole('checkbox', { name: 'Allow OCR' });
    expect(text('documents-convert-by-help')).toBe('Who can turn a PDF into Markdown with Docling.');
    expect(text('documents-presets-help')).toBe('Accurate runs a vision model on every page: the slowest.');
    expect(text('documents-ocr-help')).toBe('Reads scanned pages. Slow on long scans.');
    expect(text('documents-picture-description-help')).toBe('Runs a vision model on every picture.');
    expect(text('documents-max-pages-help')).toBe(
      'Pages one conversion may take; 0 = no limit. A longer PDF can still be converted a page range at a time.',
    );
    expect(screen.getByLabelText('Largest conversion')).toHaveAttribute(
      'aria-describedby', expect.stringContaining('documents-max-pages-source'),
    );
    expect(screen.getByRole('group', { name: 'Presets allowed' })).toHaveAttribute(
      'aria-describedby', 'documents-presets-help documents-presets-source',
    );
  });

  it('offers who may convert as the reader capability or the Docling role', async () => {
    mount({ put: () => json(200, view({ convert_by: row('DOCLING_CONVERT_BY', 'role', 'readers', { source: 'admin' }) })) });
    const readers = await screen.findByRole('radio', { name: 'Everyone with the reader capability' });
    const role = screen.getByRole('radio', { name: 'Only people with the Docling role' });
    expect(readers).toBeChecked();
    expect(role).toHaveAccessibleDescription('Give the role under People, or in Keycloak. Admins can always convert.');
    fireEvent.click(role);
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ changes: { convert_by: 'role' } });
  });

  it('turns off "everyone" when the server setting requires the role, and says so', async () => {
    mount({ initial: view({ convert_by: row('DOCLING_CONVERT_BY', 'role', 'role', { source: 'env', fallback_source: 'env' }) }) });
    const readers = await screen.findByRole('radio', { name: 'Everyone with the reader capability' });
    expect(readers).toBeDisabled();
    expect(readers).toHaveAccessibleDescription('Turned off by the server setting DOCLING_CONVERT_BY.');
    expect(screen.getByRole('radio', { name: 'Only people with the Docling role' })).toBeChecked();
  });

  it('disables what the server setting turns off, and says so', async () => {
    mount({ initial: view({
      presets: row('DOCLING_PRESETS', ['fast', 'standard'], ['fast', 'standard'], { source: 'env', fallback_source: 'env' }),
    }) });
    const accurate = await screen.findByRole('checkbox', { name: 'Accurate' });
    expect(accurate).toBeDisabled();
    expect(accurate).not.toBeChecked();
    expect(accurate).toHaveAccessibleDescription('Turned off by the server setting DOCLING_PRESETS.');
    expect(screen.getByRole('checkbox', { name: 'Fast' })).toBeEnabled();
  });

  it('disables a switch the server setting turns off, and caps the page limit with a nonzero server value', async () => {
    mount({ initial: view({
      picture_description: row('DOCLING_ALLOW_PICTURE_DESCRIPTION', false, false, { source: 'env', fallback_source: 'env' }),
      max_pages: row('DOCLING_MAX_PAGES', 100, 100, { source: 'env', fallback_source: 'env' }),
    }) });
    const pictures = await screen.findByRole('checkbox', { name: 'Allow picture descriptions' });
    expect(pictures).toBeDisabled();
    expect(pictures).toHaveAccessibleDescription('Turned off by the server setting DOCLING_ALLOW_PICTURE_DESCRIPTION.');
    expect(screen.getByRole('checkbox', { name: 'Allow OCR' })).toBeEnabled();
    expect(document.getElementById('documents-max-pages-limit')).toHaveTextContent(
      'The server setting DOCLING_MAX_PAGES allows at most 100.',
    );
  });

  it('keeps at least one preset', async () => {
    mount({ initial: view({ presets: row('DOCLING_PRESETS', ['standard'], ALL, { source: 'admin' }) }) });
    const standard = await screen.findByRole('checkbox', { name: 'Standard' });
    fireEvent.click(standard);
    expect(screen.getByText('Keep at least one preset.')).toBeInTheDocument();
    expect(save()).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Fast' }));
    expect(screen.queryByText('Keep at least one preset.')).toBeNull();
    expect(save()).toBeEnabled();
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ changes: { presets: ['fast'] } });
  });

  it('refuses a page limit that is not a whole number from 0 to 100000', async () => {
    mount();
    const pages = await screen.findByLabelText('Largest conversion');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Allow OCR' }));   // something to save, so only the limit blocks Save
    for (const bad of ['1.5', '-1', '100001', '']) {
      fireEvent.change(pages, { target: { value: bad } });
      expect(screen.getByText('Must be a whole number from 0 to 100000.')).toBeInTheDocument();
      expect(save()).toBeDisabled();
    }
    fireEvent.change(pages, { target: { value: '0' } });
    expect(screen.queryByText('Must be a whole number from 0 to 100000.')).toBeNull();
    expect(save()).toBeEnabled();
  });

  it('notes that Accurate is off while OCR is off, without changing the presets', async () => {
    mount();
    const note = 'Accurate runs OCR, so it\'s off while OCR is off.';
    const accurate = await screen.findByRole('checkbox', { name: 'Accurate' });
    expect(screen.queryByText(note)).toBeNull();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Allow OCR' }));
    expect(screen.getByText(note)).toBeInTheDocument();
    expect(accurate).toBeChecked();
    expect(accurate).toBeEnabled();
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ changes: { ocr: false } });
  });

  it('shows the note for an OCR that is already off when the page opens', async () => {
    mount({ initial: view({ ocr: row('DOCLING_ALLOW_OCR', false, false, { source: 'env', fallback_source: 'env' }) }) });
    expect(await screen.findByText('Accurate runs OCR, so it\'s off while OCR is off.')).toBeInTheDocument();
  });

  it('reports unsaved changes', async () => {
    const { onDirtyChange } = mount({ put: () => json(200, view({ ocr: row('DOCLING_ALLOW_OCR', false, true, { source: 'admin' }) })) });
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Allow OCR' }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(save());
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });

  it('goes back to the server setting for a row', async () => {
    mount({
      initial: view({ max_pages: row('DOCLING_MAX_PAGES', 200, 0, { source: 'admin' }) }),
      put: () => json(200, view()),
    });
    const pages = await screen.findByLabelText('Largest conversion');
    expect(pages).toHaveValue(200);
    fireEvent.change(pages, { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use the server setting' }));
    expect(screen.getByLabelText('Largest conversion')).toHaveValue(0);
    expect(screen.queryByText('Must be a whole number from 0 to 100000.')).toBeNull();
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ changes: { max_pages: null } });
  });

  it('shows a refused save inline and keeps the edit', async () => {
    mount({ put: () => json(422, { detail: 'Your admin refused that.' }) });
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Allow OCR' }));
    fireEvent.click(save());
    expect(await screen.findByRole('alert')).toHaveTextContent('Your admin refused that.');
    expect(screen.getByRole('checkbox', { name: 'Allow OCR' })).not.toBeChecked();
  });

  it('says when Docling is off on this server', async () => {
    mount({ initial: view({}, INFO.map((r) => (r.id === 'docling' ? { ...r, value: 'Off' } : r))) });
    await screen.findByRole('checkbox', { name: 'Allow OCR' });
    expect(screen.getByRole('status')).toHaveTextContent(
      'Docling is off on this server (DOCLING_ENABLED). These limits apply once it\'s on.',
    );
  });

  it('has no off banner when Docling is on', async () => {
    mount();
    await screen.findByRole('checkbox', { name: 'Allow OCR' });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('shows the upload limits read-only', async () => {
    mount();
    await screen.findByRole('checkbox', { name: 'Allow OCR' });
    expect(screen.getByTestId('upload-limits')).toHaveTextContent(
      'PDF uploads up to 50 MB and other files up to 10 MB (PDF_UPLOAD_MAX_MB, TEXT_UPLOAD_MAX_MB). A proxy in front of the server can set a lower limit.',
    );
  });

  it('says plainly when the limits cannot be loaded', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    render(<DocumentsSection theme={theme} apiHost="" apiPort="" />);
    expect(await screen.findByText('Couldn\'t load the document settings.')).toBeInTheDocument();
  });
});
