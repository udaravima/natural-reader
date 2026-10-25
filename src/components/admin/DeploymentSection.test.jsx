import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { DeploymentSection } from './DeploymentSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const tz = (over = {}) => ({ value: 'UTC', source: 'default', env: 'TZ', fallback: 'UTC', fallback_source: 'default', ...over });
const view = (timezone = tz(), info = []) => ({ settings: { timezone }, info });

const INFO = [
  { id: 'provider:cloud', label: 'Provider cloud', value: 'openai · https://llm.example.com/v1', env: 'INFERENCE_CLOUD_URL', source: 'env' },
  { id: 'reader_model', label: 'Reader model', value: 'kokoro', env: null, source: 'default' },
  { id: 'session_secret', label: 'Session secret', value: 'set', env: 'SESSION_SECRET', source: 'env' },
  { id: 'oidc_client_secret', label: 'OIDC client secret', value: 'not set', env: 'OIDC_CLIENT_SECRET', source: 'default' },
];

function mount({ initial = view(tz(), INFO), put, props = {} } = {}) {
  const onDirtyChange = vi.fn();
  const onSaved = vi.fn();
  const showToast = vi.fn();
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (path !== '/v1/admin/settings/deployment') return json(404, {});
    if (opts?.method === 'PUT') return put ? put(opts) : json(200, initial);
    return json(200, initial);
  });
  render(
    <DeploymentSection theme={theme} apiHost="" apiPort="" showToast={showToast}
      onDirtyChange={onDirtyChange} onSaved={onSaved} {...props} />,
  );
  return { onDirtyChange, onSaved, showToast };
}

const puts = () => apiFetch.mock.calls.filter(([, , , o]) => o?.method === 'PUT');
const zoneSelect = () => screen.findByLabelText('Timezone');

describe('DeploymentSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('saves a chosen zone and reports unsaved changes until then', async () => {
    const saved = view(tz({ value: 'Asia/Colombo', source: 'admin' }), INFO);
    const { onDirtyChange, onSaved, showToast } = mount({ put: () => json(200, saved) });
    fireEvent.change(await zoneSelect(), { target: { value: 'Asia/Colombo' } });
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('Asia/Colombo'));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ changes: { timezone: 'Asia/Colombo' } });
    expect(document.getElementById('deployment-timezone-source')).toHaveTextContent('Set here.');
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    expect(showToast).toHaveBeenCalledWith('Timezone saved. It applies from the next request.', expect.any(Number));
  });

  it('explains the timezone and ties the help and source to the control', async () => {
    mount();
    const select = await zoneSelect();
    expect(document.getElementById('deployment-timezone-help')).toHaveTextContent(
      "Decides when each day's token budget resets and which day usage counts toward. Changing it moves today's reset once, so someone may get a fresh budget a few hours early or late.",
    );
    expect(select).toHaveAttribute('aria-describedby', 'deployment-timezone-help deployment-timezone-source');
  });

  it('goes back to the server setting', async () => {
    const stored = view(tz({ value: 'Asia/Colombo', source: 'admin', fallback: 'Europe/Paris', fallback_source: 'env' }), INFO);
    const { onSaved } = mount({ initial: stored, put: () => json(200, view(tz({ value: 'Europe/Paris', source: 'env' }), INFO)) });
    await zoneSelect();
    fireEvent.click(screen.getByRole('button', { name: 'Use the server setting' }));
    expect(screen.getByLabelText('Timezone')).toHaveValue('Europe/Paris');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ changes: { timezone: null } });
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('Europe/Paris'));
  });

  it('shows a refused save inline', async () => {
    const { onSaved, onDirtyChange } = mount({ put: () => json(400, { detail: 'Unknown timezone.' }) });
    fireEvent.change(await zoneSelect(), { target: { value: 'Asia/Colombo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Unknown timezone.');
    expect(onSaved).not.toHaveBeenCalled();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
  });

  it('lists the read-only values with their source, and secrets only as set or not set', async () => {
    mount();
    expect(await screen.findByText('Server settings (read-only)')).toBeInTheDocument();
    const cloud = screen.getByTestId('info-provider:cloud');
    expect(within(cloud).getByText('Provider cloud')).toBeInTheDocument();
    expect(within(cloud).getByText('openai · https://llm.example.com/v1')).toBeInTheDocument();
    expect(cloud).toHaveTextContent('From the server setting INFERENCE_CLOUD_URL.');
    expect(cloud.querySelector('code')).toHaveTextContent('INFERENCE_CLOUD_URL');
    expect(screen.getByTestId('info-reader_model')).toHaveTextContent('The default.');
    expect(within(screen.getByTestId('info-session_secret')).getByText('set')).toBeInTheDocument();
    expect(within(screen.getByTestId('info-oidc_client_secret')).getByText('not set')).toBeInTheDocument();
  });

  it('says so when the settings cannot be loaded', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    render(<DeploymentSection theme={theme} apiHost="" apiPort="" showToast={vi.fn()} onDirtyChange={vi.fn()} onSaved={vi.fn()} />);
    expect(await screen.findByText("Couldn't load the deployment settings.")).toBeInTheDocument();
  });
});
