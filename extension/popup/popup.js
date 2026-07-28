import { getSettings, setSettings } from '../shared/settings.js';
import { VOICES } from '../shared/voices.js';
import { health } from '../shared/api.js';

const voiceSel = document.getElementById('voice');
const speed = document.getElementById('speed');
const speedVal = document.getElementById('speedVal');
const dot = document.getElementById('dot');

async function init() {
    const s = await getSettings();
    for (const v of VOICES) {
        const opt = document.createElement('option');
        opt.value = v.id; opt.textContent = v.name;
        if (v.id === s.voice) opt.selected = true;
        voiceSel.appendChild(opt);
    }
    speed.value = String(s.speed);
    speedVal.textContent = `${s.speed}×`;

    const ok = await health({ baseUrl: s.baseUrl });
    dot.classList.add(ok ? 'ok' : 'bad');
}

voiceSel.addEventListener('change', () => setSettings({ voice: voiceSel.value }));
speed.addEventListener('input', () => { speedVal.textContent = `${speed.value}×`; });
speed.addEventListener('change', () => setSettings({ speed: parseFloat(speed.value) }));
document.getElementById('openOptions').addEventListener('click', () => chrome.runtime.openOptionsPage());

for (const btn of document.querySelectorAll('button.read')) {
    btn.addEventListener('click', async () => {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab || tab.id == null) return;
        await chrome.runtime.sendMessage({ type: 'read-aloud:popup-read', tabId: tab.id, mode: btn.dataset.mode });
        window.close();
    });
}

init();
