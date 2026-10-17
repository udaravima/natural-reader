// The read-aloud toolbar. It runs as an extension page inside an iframe the
// content script places on the page, so its audio plays under the extension's
// policy, not the page's: a page CSP such as GitHub's media-src refuses the
// blob: audio a page-context player needs, silently. The page sees only the
// frame and the size / move / close notes posted to it below.
import { getSettings } from '../shared/settings.js';
import { VOICES } from '../shared/voices.js';
import { base64ToBlob } from '../shared/api.js';

const sid = location.hash.slice(1);
const parentOrigin = (location.ancestorOrigins && location.ancestorOrigins[0]) || '*';
const tell = (type, extra = {}) => parent.postMessage({ nr: sid, type, ...extra }, parentOrigin);

const bar = document.querySelector('.bar');
const statusEl = bar.querySelector('.status');
const toggleBtn = bar.querySelector('[data-act="toggle"]');
const progress = bar.querySelector('.progress');
const voiceSel = bar.querySelector('[data-act="voice"]');
const speedIn = bar.querySelector('[data-act="speed"]');

// The frame is sized to the bar, plus room for its shadow.
new ResizeObserver(() => {
    const r = bar.getBoundingClientRect();
    tell('size', { w: Math.ceil(r.width) + 16, h: Math.ceil(r.height) + 16 });
}).observe(bar);
document.body.style.padding = '8px';

// Dragging: the content script moves the frame. Positions are absolute screen
// coordinates (the frame moves under the pointer, so frame coordinates would
// drift), and a pointer that outruns the frame lands on the content script's
// drag layer instead, which reports the same coordinates.
const handle = bar.querySelector('.handle');
let dragging = false;
handle.addEventListener('pointerdown', (e) => {
    dragging = true;
    handle.setPointerCapture(e.pointerId);
    tell('drag', { phase: 'start', sx: e.screenX, sy: e.screenY });
});
handle.addEventListener('pointermove', (e) => {
    if (dragging) tell('drag', { phase: 'move', sx: e.screenX, sy: e.screenY });
});
const endDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    if (handle.hasPointerCapture(e.pointerId)) handle.releasePointerCapture(e.pointerId);
    tell('drag', { phase: 'end' });
};
handle.addEventListener('pointerup', endDrag);
handle.addEventListener('pointercancel', endDrag);

function showError(err) {
    bar.classList.add('error');
    statusEl.textContent = String(err && err.message ? err.message : err);
    statusEl.title = statusEl.textContent;
}

async function synthesize({ sentences, voice, speed }) {
    const reply = await chrome.runtime.sendMessage({ type: 'read-aloud:synthesize', sentences, voice, speed });
    if (!reply || !reply.ok) throw new Error((reply && reply.error) || 'TTS request failed');
    return base64ToBlob(reply.audio);
}

async function main() {
    const session = await chrome.runtime.sendMessage({ type: 'read-aloud:session', sid });
    if (!session) { tell('close'); return; }
    const { chunks } = session;
    const state = await getSettings();

    for (const v of VOICES) {
        const opt = document.createElement('option');
        opt.value = v.id; opt.textContent = v.name;
        if (v.id === state.voice) opt.selected = true;
        voiceSel.appendChild(opt);
    }
    speedIn.value = String(state.speed);

    const audio = new Audio();
    const urls = [];
    let stopped = false;
    const finish = () => {
        stopped = true;
        audio.pause();
        urls.forEach((u) => URL.revokeObjectURL(u));
        tell('close');
    };
    bar.querySelector('[data-act="close"]').addEventListener('click', finish);
    bar.querySelector('[data-act="stop"]').addEventListener('click', finish);
    const showPlaying = (playing) => {
        toggleBtn.textContent = playing ? '⏸' : '▶';
        toggleBtn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    };
    toggleBtn.addEventListener('click', () => {
        if (audio.paused) audio.play().then(() => showPlaying(true)).catch(() => {});
        else { audio.pause(); showPlaying(false); }
    });
    voiceSel.addEventListener('change', () => { state.voice = voiceSel.value; });
    speedIn.addEventListener('input', () => { state.speed = parseFloat(speedIn.value); }); // applies at synth time
    audio.addEventListener('timeupdate', () => {
        if (audio.duration) progress.value = String((audio.currentTime / audio.duration) * 100);
    });
    progress.addEventListener('input', () => {
        if (audio.duration) audio.currentTime = (Number(progress.value) / 100) * audio.duration;
    });

    // Prefetch-one-ahead queue.
    const cache = new Map(); // index -> Promise<Blob>
    const fetchChunk = (i) => {
        if (i >= chunks.length) return null;
        if (!cache.has(i)) {
            cache.set(i, synthesize({ sentences: chunks[i], voice: state.voice, speed: state.speed }));
        }
        return cache.get(i);
    };

    async function playIndex(i) {
        if (stopped) return;
        if (i >= chunks.length) { finish(); return; }
        statusEl.textContent = chunks.length > 1 ? `chunk ${i + 1} / ${chunks.length}` : 'reading';
        let blob;
        try { blob = await fetchChunk(i); } catch (err) { showError(err); return; }
        if (stopped) return;
        const url = URL.createObjectURL(blob);
        urls.push(url);
        audio.src = url;
        const prefetch = fetchChunk(i + 1); // prefetch next while this plays
        if (prefetch) prefetch.catch(() => {});
        audio.onended = () => playIndex(i + 1);
        try {
            await audio.play();
            showPlaying(true);
        } catch {
            // Chrome's autoplay rules can refuse a start with no click in this
            // frame; the audio is loaded, so one press of ▶ starts it.
            showPlaying(false);
            statusEl.textContent = 'press ▶ to start';
        }
    }

    playIndex(0);
}

main().catch(showError);
