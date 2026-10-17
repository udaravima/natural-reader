import { getSettings, setSettings, getToken, setToken, originPattern } from '../shared/settings.js';
import { VOICES } from '../shared/voices.js';
import { checkConnection, CONNECTION_TEXT } from '../shared/api.js';

const voice = document.getElementById('voice');
const speed = document.getElementById('speed');
const speedVal = document.getElementById('speedVal');
const baseUrl = document.getElementById('baseUrl');
const token = document.getElementById('token');
const testResult = document.getElementById('testResult');

async function init() {
    const s = await getSettings();
    for (const v of VOICES) {
        const opt = document.createElement('option');
        opt.value = v.id; opt.textContent = v.name;
        if (v.id === s.voice) opt.selected = true;
        voice.appendChild(opt);
    }
    speed.value = String(s.speed);
    speedVal.textContent = `${s.speed}×`;
    baseUrl.value = s.baseUrl;
    token.value = await getToken();
}

// localhost is granted at install; any other server needs the user to allow
// it. Chrome prompts only during a click, so this is called first thing in the
// Test connection handler. An already-granted origin resolves true, no prompt.
function ensureHostAccess(url) {
    const pattern = originPattern(url);
    if (!pattern) return Promise.resolve(false);
    return chrome.permissions.request({ origins: [pattern] }).catch(() => false);
}

voice.addEventListener('change', () => setSettings({ voice: voice.value }));
speed.addEventListener('input', () => { speedVal.textContent = `${speed.value}×`; });
speed.addEventListener('change', () => setSettings({ speed: parseFloat(speed.value) }));
baseUrl.addEventListener('change', async () => {
    let v = baseUrl.value.trim().replace(/\/+$/, '');
    if (!v) v = 'http://localhost:8000';
    baseUrl.value = v;            // reflect the normalized value back into the field
    await setSettings({ baseUrl: v });
    const pattern = originPattern(v);
    if (pattern && !(await chrome.permissions.contains({ origins: [pattern] }))) {
        testResult.textContent = 'Click Test connection to allow access to this server.';
    }
});
token.addEventListener('change', async () => {
    await setToken(token.value);
    token.value = await getToken();
});

document.getElementById('test').addEventListener('click', async () => {
    const url = baseUrl.value.trim().replace(/\/+$/, '');
    // Ask first: the prompt must start inside the click, before any await.
    const allowed = await ensureHostAccess(url);
    if (!allowed) { testResult.textContent = '✗ Access to this server was not allowed'; return; }
    await setToken(token.value);
    testResult.textContent = 'Testing…';
    const r = await checkConnection({ baseUrl: url, token: await getToken() });
    testResult.textContent = r.ok
        ? `✓ ${CONNECTION_TEXT.ok}${r.email ? ` as ${r.email}` : ''}`
        : `✗ ${CONNECTION_TEXT[r.reason]}`;
});

init();
