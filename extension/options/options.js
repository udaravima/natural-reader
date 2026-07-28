import { getSettings, setSettings } from '../shared/settings.js';
import { VOICES } from '../shared/voices.js';
import { health } from '../shared/api.js';

const voice = document.getElementById('voice');
const speed = document.getElementById('speed');
const speedVal = document.getElementById('speedVal');
const baseUrl = document.getElementById('baseUrl');
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
}

voice.addEventListener('change', () => setSettings({ voice: voice.value }));
speed.addEventListener('input', () => { speedVal.textContent = `${speed.value}×`; });
speed.addEventListener('change', () => setSettings({ speed: parseFloat(speed.value) }));
baseUrl.addEventListener('change', () => {
    let v = baseUrl.value.trim().replace(/\/+$/, '');
    if (!v) v = 'http://localhost:8000';
    baseUrl.value = v;            // reflect the normalized value back into the field
    setSettings({ baseUrl: v });
});

document.getElementById('test').addEventListener('click', async () => {
    testResult.textContent = 'Testing…';
    const ok = await health({ baseUrl: baseUrl.value.trim() });
    testResult.textContent = ok ? '✓ Connected' : '✗ Not reachable';
});

init();
