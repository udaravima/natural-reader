import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatOptionsSection } from './ChatOptionsSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const view = (over = {}) => ({ web_search: 'ask', source: 'default', searxng: true, ...over });

const GET = '/v1/admin/chat-options';
const puts = () => apiFetch.mock.calls.filter(([, , , o]) => o?.method === 'PUT');
const putBody = (i = 0) => JSON.parse(puts()[i][3].body);
const radio = (name) => screen.getByRole('radio', { name });

// `onPut(body)` answers a PUT: a view, or a ready-made response (for a failure or a held-open request).
function mount(initial, { onPut = (body) => view({ web_search: body.web_search ?? 'ask', source: body.web_search ? 'admin' : 'default' }), onSaved } = {}) {
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (path === GET && opts?.method === 'PUT') {
      const out = await onPut(JSON.parse(opts.body));
      return out && 'ok' in out ? out : json(200, out);
    }
    if (path === GET) return json(200, initial);
    return json(404, {});
  });
  const showToast = vi.fn();
  render(<ChatOptionsSection theme={theme} apiHost="" apiPort="" showToast={showToast} onSaved={onSaved} />);
  return showToast;
}

describe('ChatOptionsSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the saved mode', async () => {
    mount(view({ web_search: 'auto', source: 'admin' }));
    expect(await screen.findByRole('region', { name: 'Chat options' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Chat options' })).toBeTruthy();
    // The section is on screen while it still loads: wait for the radios themselves.
    expect((await screen.findByRole('radio', { name: 'Search without asking' })).checked).toBe(true);
    expect(radio('Ask first').checked).toBe(false);
    expect(radio('Off').checked).toBe(false);
  });

  it('is one real radio group named Web search, so the arrow keys move between the choices', async () => {
    mount(view());
    const group = await screen.findByRole('group', { name: 'Web search' });
    const radios = screen.getAllByRole('radio');
    expect(radios.map((r) => group.contains(r))).toEqual([true, true, true]);
    // Native radios that share a name are one tab stop and answer the arrow keys in the browser.
    expect(new Set(radios.map((r) => r.name)).size).toBe(1);
    expect(radios[0].name).not.toBe('');
  });

  it('changing saves and toasts', async () => {
    const showToast = mount(view());
    fireEvent.click(await screen.findByRole('radio', { name: 'Search without asking' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(apiFetch).toHaveBeenCalledWith('', '', GET, expect.objectContaining({ method: 'PUT' }));
    expect(putBody()).toEqual({ web_search: 'auto' });
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'Web search: Search without asking. Applies from the next message.', expect.any(Number)));
    expect(radio('Search without asking').checked).toBe(true);
    expect(await screen.findByText('Set here.')).toBeTruthy();
  });

  it("shows the server's answer to a change, not what the console assumed", async () => {
    // The console shows a choice at once and assumes "Set here." and the mode it sent; the answer wins. (Here
    // something else decided: a change made elsewhere at the same time.)
    const showToast = mount(view({ source: 'default' }), { onPut: () => view({ web_search: 'off', source: 'env' }) });
    fireEvent.click(await screen.findByRole('radio', { name: 'Search without asking' }));
    expect(await screen.findByText('From the server setting CHAT_WEB_SEARCH.')).toBeTruthy();
    expect(radio('Off').checked).toBe(true);
    expect(radio('Search without asking').checked).toBe(false);
    expect(screen.queryByText('Set here.')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Use the server setting' })).toBeNull();
    expect(showToast).toHaveBeenCalledWith('Web search: Off. Applies from the next message.', expect.any(Number));
  });

  it('does nothing when the chosen mode is clicked again', async () => {
    mount(view({ web_search: 'off', source: 'admin' }));
    fireEvent.click(await screen.findByRole('radio', { name: 'Off' }));
    expect(puts()).toHaveLength(0);
  });

  it('is disabled without SearXNG, and says why', async () => {
    mount(view({ searxng: false }));
    expect(await screen.findByText("Web search isn't set up on this server (SEARXNG_URL).")).toBeTruthy();
    for (const name of ['Ask first', 'Search without asking', 'Off']) expect(radio(name).disabled).toBe(true);
  });

  it('describes the group by where the mode comes from, and by why it is disabled', async () => {
    mount(view({ searxng: false, source: 'env' }));
    const group = await screen.findByRole('group', { name: 'Web search' });
    expect(group).toHaveAccessibleDescription(
      "Web search isn't set up on this server (SEARXNG_URL). From the server setting CHAT_WEB_SEARCH.");
  });

  it('describes the group by the source line alone when SearXNG is set up', async () => {
    mount(view({ source: 'admin' }));
    expect(await screen.findByRole('group', { name: 'Web search' })).toHaveAccessibleDescription('Set here.');
  });

  it('does not mention SearXNG when it is set up', async () => {
    mount(view());
    await screen.findByRole('radio', { name: 'Off' });
    expect(screen.queryByText(/isn't set up/)).toBeNull();
  });

  it('a failed save restores the previous choice and the previous source', async () => {
    const showToast = mount(view({ web_search: 'ask', source: 'env' }),
      { onPut: () => json(500, { detail: 'database is down' }) });
    fireEvent.click(await screen.findByRole('radio', { name: 'Off' }));
    // The toast is sent before React commits the restore: wait for the screen, then read the toast.
    await waitFor(() => {
      expect(radio('Ask first').checked).toBe(true);
      expect(radio('Off').disabled).toBe(false);
    });
    expect(radio('Off').checked).toBe(false);
    expect(screen.getByText('From the server setting CHAT_WEB_SEARCH.')).toBeTruthy();   // not "Set here."
    expect(screen.queryByRole('button', { name: 'Use the server setting' })).toBeNull();
    expect(showToast).toHaveBeenCalledWith("That didn't work: database is down", expect.any(Number));
  });

  it('a failed save without a readable detail says the HTTP status', async () => {
    const showToast = mount(view(), { onPut: () => ({ ok: false, status: 502, json: async () => { throw new Error('not json'); } }) });
    fireEvent.click(await screen.findByRole('radio', { name: 'Off' }));
    await waitFor(() => {
      expect(radio('Ask first').checked).toBe(true);
      expect(radio('Off').disabled).toBe(false);
    });
    expect(showToast).toHaveBeenCalledWith("That didn't work: HTTP 502", expect.any(Number));
  });

  it.each([
    ['admin', 'Set here.'],
    ['env', 'From the server setting CHAT_WEB_SEARCH.'],
    ['default', 'The default.'],
  ])('says where the mode comes from: %s', async (source, line) => {
    mount(view({ source }));
    expect(await screen.findByText(line)).toBeTruthy();
  });

  it('offers "Use the server setting" only when the admin has set the mode', async () => {
    mount(view({ web_search: 'auto', source: 'env' }));
    await screen.findByText('From the server setting CHAT_WEB_SEARCH.');
    expect(screen.queryByRole('button', { name: 'Use the server setting' })).toBeNull();
  });

  it.each([
    [{ web_search: 'off', source: 'env', searxng: true }, 'Off', 'From the server setting CHAT_WEB_SEARCH.'],
    [{ web_search: 'ask', source: 'default', searxng: true }, 'Ask first', 'The default.'],
  ])('"Use the server setting" sends null and shows what comes back: %o', async (back, label, line) => {
    const showToast = mount(view({ web_search: 'auto', source: 'admin' }), { onPut: () => back });
    fireEvent.click(await screen.findByRole('button', { name: 'Use the server setting' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(putBody()).toEqual({ web_search: null });
    expect(await screen.findByText(line)).toBeTruthy();
    expect(radio(label).checked).toBe(true);
    expect(showToast).toHaveBeenCalledWith(`Web search: ${label}. Applies from the next message.`, expect.any(Number));
    // The admin's row is gone, so there is nothing left to give up.
    expect(screen.queryByRole('button', { name: 'Use the server setting' })).toBeNull();
  });

  it('a failed "Use the server setting" changes nothing', async () => {
    const showToast = mount(view({ web_search: 'auto', source: 'admin' }),
      { onPut: () => json(500, { detail: 'nope' }) });
    fireEvent.click(await screen.findByRole('button', { name: 'Use the server setting' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Use the server setting' }).disabled).toBe(false));
    expect(radio('Search without asking').checked).toBe(true);
    expect(screen.getByText('Set here.')).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith("That didn't work: nope", expect.any(Number));
  });

  it('disables the radios and the reset while a save is in flight', async () => {
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    mount(view({ web_search: 'ask', source: 'admin' }), { onPut: () => held });
    fireEvent.click(await screen.findByRole('radio', { name: 'Off' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    for (const name of ['Ask first', 'Search without asking', 'Off']) expect(radio(name).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Use the server setting' }).disabled).toBe(true);
    // A second click while the first save is out sends nothing.
    fireEvent.click(radio('Search without asking'));
    expect(puts()).toHaveLength(1);
    release(json(200, view({ web_search: 'off', source: 'admin' })));
    await waitFor(() => expect(radio('Search without asking').disabled).toBe(false));
    expect(radio('Off').checked).toBe(true);
    expect(screen.getByRole('button', { name: 'Use the server setting' }).disabled).toBe(false);
  });

  it('disables the reset while a reset is in flight', async () => {
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    mount(view({ web_search: 'auto', source: 'admin' }), { onPut: () => held });
    fireEvent.click(await screen.findByRole('button', { name: 'Use the server setting' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(radio('Off').disabled).toBe(true);
    release(json(200, view()));
    await waitFor(() => expect(radio('Off').disabled).toBe(false));
  });

  it('tells the console after a save that took, and not after one that failed', async () => {
    const onSaved = vi.fn();
    const failing = { current: true };
    mount(view(), { onSaved, onPut: (body) => (failing.current ? json(500, { detail: 'x' }) : view({ web_search: body.web_search, source: 'admin' })) });
    fireEvent.click(await screen.findByRole('radio', { name: 'Off' }));
    await waitFor(() => expect(radio('Ask first').disabled).toBe(false));
    expect(onSaved).not.toHaveBeenCalled();
    failing.current = false;
    fireEvent.click(radio('Off'));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  });

  it('puts the keyboard focus back on the chosen radio once the save is done', async () => {
    // The radios are disabled while a save is out, and a browser drops the focus from a control that gets
    // disabled: without this, ArrowDown twice in a row would stop after the first.
    mount(view());
    const ask = await screen.findByRole('radio', { name: 'Ask first' });
    ask.focus();
    fireEvent.click(radio('Off'));
    await waitFor(() => expect(radio('Off').disabled).toBe(false));
    expect(radio('Off').checked).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(radio('Off')));
  });

  it('puts the focus back when the browser dropped it because the radio was disabled', async () => {
    // What a browser does to a focused control that gets disabled: focus falls to the page.
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    mount(view(), { onPut: () => held });
    (await screen.findByRole('radio', { name: 'Ask first' })).focus();
    fireEvent.click(radio('Off'));
    await waitFor(() => expect(radio('Off').disabled).toBe(true));
    document.activeElement.blur();
    release(json(200, view({ web_search: 'off', source: 'admin' })));
    await waitFor(() => expect(radio('Off').disabled).toBe(false));
    await waitFor(() => expect(document.activeElement).toBe(radio('Off')));
  });

  it('does not steal the focus when the admin moved to another field during the save', async () => {
    // Otherwise the next arrow key would change a deployment-wide setting from a field they never meant to leave.
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    mount(view(), { onPut: () => held });
    (await screen.findByRole('radio', { name: 'Ask first' })).focus();
    fireEvent.click(radio('Off'));
    await waitFor(() => expect(radio('Off').disabled).toBe(true));
    const field = document.createElement('textarea');
    document.body.appendChild(field);
    try {
      field.focus();
      release(json(200, view({ web_search: 'off', source: 'admin' })));
      await waitFor(() => expect(radio('Off').disabled).toBe(false));
      expect(radio('Off').checked).toBe(true);
      expect(document.activeElement).toBe(field);
    } finally {
      field.remove();
    }
  });

  it('leaves the focus alone when it was not in the options', async () => {
    mount(view());
    await screen.findByRole('radio', { name: 'Ask first' });
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    try {
      outside.focus();
      fireEvent.click(radio('Off'));
      await waitFor(() => expect(radio('Off').disabled).toBe(false));
      expect(document.activeElement).toBe(outside);
    } finally {
      outside.remove();
    }
  });

  it('says so when the options cannot be loaded', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    render(<ChatOptionsSection theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
    expect(await screen.findByText(/Couldn't load the chat options/)).toBeTruthy();
  });
});
