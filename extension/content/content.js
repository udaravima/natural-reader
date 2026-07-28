if (!window.__readAloudInjected) {
    window.__readAloudInjected = true;

    const RA = {};
    let activeCleanup = null;

    async function loadShared() {
        const base = chrome.runtime.getURL('shared/');
        const [text, api, settings, voices] = await Promise.all([
            import(base + 'text.js'),
            import(base + 'api.js'),
            import(base + 'settings.js'),
            import(base + 'voices.js'),
        ]);
        return { ...text, ...api, ...settings, VOICES: voices.VOICES };
    }

    // ---- Toolbar (Shadow DOM) -------------------------------------------
    async function buildToolbar(mods, state) {
        const host = document.createElement('div');
        host.id = '__read_aloud_host';
        document.documentElement.appendChild(host);
        const root = host.attachShadow({ mode: 'open' });

        const style = document.createElement('style');
        const cssUrl = chrome.runtime.getURL('content/toolbar.css');
        style.textContent = await (await fetch(cssUrl)).text();
        root.appendChild(style);

        const bar = document.createElement('div');
        bar.className = 'bar';
        bar.innerHTML = `
            <span class="handle" title="Drag">⠿</span>
            <button data-act="toggle">⏸</button>
            <button data-act="stop">⏹</button>
            <input class="progress" type="range" min="0" max="100" value="0" />
            <select data-act="voice"></select>
            <label>Speed <input data-act="speed" type="range" min="0.5" max="2" step="0.1" /></label>
            <span class="status"></span>
            <button data-act="close">✕</button>
        `;
        root.appendChild(bar);

        const voiceSel = bar.querySelector('[data-act="voice"]');
        for (const v of mods.VOICES) {
            const opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = v.name; // textContent — never innerHTML
            if (v.id === state.voice) opt.selected = true;
            voiceSel.appendChild(opt);
        }
        bar.querySelector('[data-act="speed"]').value = String(state.speed);

        makeDraggable(bar, bar.querySelector('.handle'));
        return { host, root, bar };
    }

    function makeDraggable(bar, handle) {
        let sx = 0; let sy = 0; let ox = 0; let oy = 0; let dragging = false;
        handle.addEventListener('pointerdown', (e) => {
            dragging = true; sx = e.clientX; sy = e.clientY;
            const r = bar.getBoundingClientRect(); ox = r.left; oy = r.top;
            handle.setPointerCapture(e.pointerId);
        });
        handle.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            bar.style.left = `${ox + (e.clientX - sx)}px`;
            bar.style.top = `${oy + (e.clientY - sy)}px`;
            bar.style.bottom = 'auto'; bar.style.transform = 'none';
        });
        handle.addEventListener('pointerup', (e) => { dragging = false; handle.releasePointerCapture(e.pointerId); });
    }

    // ---- Reading session ------------------------------------------------
    async function startReading(mode) {
        if (activeCleanup) { activeCleanup(); }
        try {
            const mods = RA.mods || (RA.mods = await loadShared());
            const state = await mods.getSettings();

            const raw = mods.extractSelectionOrPage(document, mode);
            const sentences = mods.splitSentences(raw);
            if (sentences.length === 0) { flashNotice(); return; }

            const chunks = mode === 'selection' ? [sentences] : mods.chunkSentences(sentences, 30);
            const ui = await buildToolbar(mods, state);
            const audio = new Audio();
            const urls = [];
            let stopped = false;

            const statusEl = ui.bar.querySelector('.status');
            const toggleBtn = ui.bar.querySelector('[data-act="toggle"]');
            const progress = ui.bar.querySelector('.progress');

            const cleanup = () => {
                stopped = true;
                audio.pause();
                urls.forEach((u) => URL.revokeObjectURL(u));
                ui.host.remove();
                activeCleanup = null;
            };
            activeCleanup = cleanup;

            ui.bar.querySelector('[data-act="close"]').addEventListener('click', cleanup);
            ui.bar.querySelector('[data-act="stop"]').addEventListener('click', cleanup);
            toggleBtn.addEventListener('click', () => {
                if (audio.paused) { audio.play(); toggleBtn.textContent = '⏸'; }
                else { audio.pause(); toggleBtn.textContent = '▶'; }
            });
            ui.bar.querySelector('[data-act="voice"]').addEventListener('change', (e) => { state.voice = e.target.value; });
            ui.bar.querySelector('[data-act="speed"]').addEventListener('input', (e) => {
                state.speed = parseFloat(e.target.value); audio.playbackRate = 1; // speed applies at synth time
            });
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
                    cache.set(i, mods.synthesize({
                        sentences: chunks[i], voice: state.voice, speed: state.speed, baseUrl: state.baseUrl,
                    }));
                }
                return cache.get(i);
            };

            async function playIndex(i) {
                if (stopped || i >= chunks.length) { if (!stopped && i >= chunks.length) cleanup(); return; }
                statusEl.textContent = chunks.length > 1 ? `chunk ${i + 1} / ${chunks.length}` : 'reading';
                let blob;
                try { blob = await fetchChunk(i); } catch (err) { showError(ui, statusEl, err); return; }
                if (stopped) return;
                const url = URL.createObjectURL(blob);
                urls.push(url);
                audio.src = url;
                const prefetch = fetchChunk(i + 1); // prefetch next while this plays
                if (prefetch) prefetch.catch(() => {});
                audio.onended = () => playIndex(i + 1);
                try { await audio.play(); } catch { /* autoplay/user-gesture edge — ignore */ }
            }

            playIndex(0);
        } catch (err) {
            flashNotice('Read Aloud: ' + (err && err.message ? err.message : 'could not start on this page'));
        }
    }

    function showError(ui, statusEl, err) {
        ui.bar.classList.add('error');
        statusEl.textContent = String(err && err.message ? err.message : err);
    }

    function flashNotice(message = 'Read Aloud: nothing to read') {
        const n = document.createElement('div');
        n.textContent = message;
        n.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#1f2430;color:#fff;padding:8px 12px;border-radius:10px;font:13px system-ui;';
        document.documentElement.appendChild(n);
        setTimeout(() => n.remove(), 2000);
    }

    chrome.runtime.onMessage.addListener((msg) => {
        if (msg && msg.type === 'read-aloud:start') startReading(msg.mode);
    });
}
