import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
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

  it('after "Use the server setting" the focus goes to the checked radio, not the page (the button is gone)', async () => {
    // The reset button unmounts once the admin's choice is gone, taking the focus with it.
    mount(view({ web_search: 'auto', source: 'admin' }), { onPut: () => view({ web_search: 'ask', source: 'default' }) });
    const reset = await screen.findByRole('button', { name: 'Use the server setting' });
    reset.focus();
    fireEvent.click(reset);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Use the server setting' })).toBeNull());
    await waitFor(() => expect(radio('Ask first').disabled).toBe(false));
    expect(radio('Ask first').checked).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(radio('Ask first')));
  });

  it('...also when the click never focused the button (a script click, or Safari, which does not focus buttons on click)', async () => {
    // Walk 6c: the focus was nowhere to begin with, so it stayed nowhere: <body>, once the button was gone.
    mount(view({ web_search: 'auto', source: 'admin' }), { onPut: () => view({ web_search: 'ask', source: 'default' }) });
    const reset = await screen.findByRole('button', { name: 'Use the server setting' });
    expect(document.activeElement).toBe(document.body);
    fireEvent.click(reset);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Use the server setting' })).toBeNull());
    await waitFor(() => expect(radio('Ask first').disabled).toBe(false));
    expect(radio('Ask first').checked).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(radio('Ask first')));
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

describe('ChatOptionsSection: Voice input', () => {
  const vradio = (name) => within(screen.getByRole('group', { name: 'Voice input' })).getByRole('radio', { name });
  beforeEach(() => vi.clearAllMocks());

  const full = (over = {}) => view({ voice_input: false, voice_source: 'default', ...over });
  // Answers a PUT the way the server does: it applies only the keys sent.
  function mountVoice(initial, { onPut } = {}) {
    let current = initial;
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (opts?.method === 'PUT') {
        const body = JSON.parse(opts.body);
        if (onPut) return onPut(body);
        if ('voice_input' in body) {
          current = { ...current, voice_input: body.voice_input ?? false, voice_source: body.voice_input === null ? 'default' : 'admin' };
        }
        if ('web_search' in body) {
          current = { ...current, web_search: body.web_search ?? 'ask', source: body.web_search === null ? 'default' : 'admin' };
        }
        return json(200, current);
      }
      return json(200, current);
    });
    const showToast = vi.fn();
    render(<ChatOptionsSection theme={theme} apiHost="" apiPort="" showToast={showToast} />);
    return showToast;
  }

  it('shows a Voice input group with On and Off, on the saved value', async () => {
    mountVoice(full({ voice_input: true, voice_source: 'admin' }));
    const group = await screen.findByRole('group', { name: 'Voice input' });
    expect(group).toBeTruthy();
    expect(vradio('On').checked).toBe(true);
    expect(vradio('Off').checked).toBe(false);
  });

  it('has no Voice input row when the server does not report it', async () => {
    mountVoice(view());
    await screen.findByRole('radio', { name: 'Ask first' });
    expect(screen.queryByRole('group', { name: 'Voice input' })).toBeNull();
  });

  it('switching it on saves only voice_input and toasts', async () => {
    const showToast = mountVoice(full());
    await screen.findByRole('group', { name: 'Voice input' });
    fireEvent.click(vradio('On'));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(putBody()).toEqual({ voice_input: true });
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('Voice input: On. Applies from the next recording.', expect.any(Number)));
    expect(vradio('On').checked).toBe(true);
    expect(await screen.findByText('Set here.')).toBeTruthy();
  });

  it('switching it off toasts Off', async () => {
    const showToast = mountVoice(full({ voice_input: true, voice_source: 'admin' }));
    await screen.findByRole('group', { name: 'Voice input' });
    fireEvent.click(vradio('Off'));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('Voice input: Off. Applies from the next recording.', expect.any(Number)));
    expect(putBody()).toEqual({ voice_input: false });
  });

  it('a web search change never sends voice_input', async () => {
    mountVoice(full());
    await screen.findByRole('group', { name: 'Voice input' });
    // two radios are named Off (web search and voice): the web one is the first in the page
    fireEvent.click(screen.getAllByRole('radio', { name: 'Off' })[0]);
    await waitFor(() => expect(puts().length).toBeGreaterThan(0));
    for (let i = 0; i < puts().length; i++) expect(putBody(i)).not.toHaveProperty('voice_input');
    expect(putBody(puts().length - 1)).toEqual({ web_search: 'off' });
  });

  it('Use the server setting for voice sends {voice_input: null} and leaves web search alone', async () => {
    mountVoice(full({ web_search: 'auto', source: 'admin', voice_input: true, voice_source: 'admin' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use the server setting for voice input' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(putBody()).toEqual({ voice_input: null });
    expect(await screen.findByText('The default.')).toBeTruthy();
    // web search kept its own source and its own reset button
    expect(screen.getByRole('button', { name: 'Use the server setting' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Search without asking' }).checked).toBe(true);
  });

  it('the web search reset sends only web_search', async () => {
    mountVoice(full({ web_search: 'auto', source: 'admin', voice_input: true, voice_source: 'admin' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use the server setting' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(putBody()).toEqual({ web_search: null });
  });

  it.each([
    ['admin', 'Set here.'],
    ['env', 'From the server setting CHAT_VOICE_INPUT.'],
    ['default', 'The default.'],
  ])('says where voice input comes from: %s', async (voice_source, line) => {
    mountVoice(full({ voice_source, source: 'env' }));
    expect(await screen.findByText(line)).toBeTruthy();
  });

  it('only shows the voice reset when the admin set it', async () => {
    mountVoice(full({ voice_source: 'env' }));
    await screen.findByRole('group', { name: 'Voice input' });
    expect(screen.queryByRole('button', { name: 'Use the server setting for voice input' })).toBeNull();
  });

  it('a failed voice save puts back the value and the source', async () => {
    const showToast = mountVoice(full({ voice_source: 'env' }), { onPut: () => json(500, { detail: 'database is down' }) });
    await screen.findByRole('group', { name: 'Voice input' });
    fireEvent.click(vradio('On'));
    await waitFor(() => expect(vradio('On').disabled).toBe(false));
    expect(vradio('Off').checked).toBe(true);
    expect(screen.getByText('From the server setting CHAT_VOICE_INPUT.')).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith("That didn't work: database is down", expect.any(Number));
  });
});
