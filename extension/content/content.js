// Places the read-aloud toolbar on the page. The toolbar is an extension page
// in an iframe (toolbar/toolbar.html): nothing here loads code or audio into
// the page, so a strict page CSP can't block it. Text extraction and chunking
// happen in the service worker, which hands over a session id.
if (!window.__readAloudInjected) {
    window.__readAloudInjected = true;

    let frame = null;
    let sid = null;

    // A drag: where it started on screen and where the frame was then. Every
    // position is computed from this, so a late or repeated event can't add up.
    let drag = null;
    let layer = null;

    function moveTo(sx, sy) {
        if (!drag || !frame) return;
        const r = frame.getBoundingClientRect();
        const left = Math.min(Math.max(drag.left + (sx - drag.sx), 40 - r.width), innerWidth - 40);
        const top = Math.min(Math.max(drag.top + (sy - drag.sy), 0), innerHeight - 24);
        frame.style.left = `${left}px`;
        frame.style.top = `${top}px`;
    }

    function endDrag() {
        drag = null;
        if (layer) layer.remove();
        layer = null;
    }

    function startDrag(sx, sy) {
        const r = frame.getBoundingClientRect();
        drag = { sx, sy, left: r.left, top: r.top };
        frame.style.left = `${r.left}px`;
        frame.style.top = `${r.top}px`;
        frame.style.bottom = 'auto';
        frame.style.transform = 'none';
        // Just under the frame: catches the pointer when it outruns the frame.
        layer = document.createElement('div');
        layer.style.cssText = 'all: initial; position: fixed; inset: 0; z-index: 2147483646; cursor: grabbing;';
        layer.addEventListener('pointermove', (e) => moveTo(e.screenX, e.screenY));
        layer.addEventListener('pointerup', endDrag);
        layer.addEventListener('pointercancel', endDrag);
        document.documentElement.appendChild(layer);
    }

    function close() {
        endDrag();
        if (frame) frame.remove();
        frame = null;
        sid = null;
    }

    function open(newSid) {
        close();
        sid = newSid;
        frame = document.createElement('iframe');
        frame.src = `${chrome.runtime.getURL('toolbar/toolbar.html')}#${sid}`;
        frame.title = 'Read aloud';
        frame.allow = 'autoplay';
        frame.style.cssText = 'all: initial; position: fixed; z-index: 2147483647; bottom: 12px; left: 50%;'
            + ' transform: translateX(-50%); width: 660px; height: 60px; max-width: 100vw;'
            + ' border: 0; background: transparent; color-scheme: normal;';
        document.documentElement.appendChild(frame);
    }

    // Only the toolbar frame itself may steer the frame: event.source can't be
    // forged by the page, and the session id must match too.
    window.addEventListener('message', (e) => {
        if (!frame || e.source !== frame.contentWindow) return;
        const m = e.data || {};
        if (m.nr !== sid) return;
        if (m.type === 'close') close();
        else if (m.type === 'size') {
            frame.style.width = `${m.w}px`;
            frame.style.height = `${m.h}px`;
        } else if (m.type === 'drag') {
            if (m.phase === 'start') startDrag(m.sx, m.sy);
            else if (m.phase === 'move') moveTo(m.sx, m.sy);
            else endDrag();
        }
    });

    function flashNotice(message) {
        const n = document.createElement('div');
        n.textContent = message;
        n.style.cssText = 'all: initial; position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);'
            + ' z-index: 2147483647; background: #1f2430; color: #fff; padding: 8px 12px; border-radius: 10px;'
            + ' font: 13px system-ui, sans-serif;';
        document.documentElement.appendChild(n);
        setTimeout(() => n.remove(), 2000);
    }

    chrome.runtime.onMessage.addListener((msg) => {
        if (msg && msg.type === 'read-aloud:open') open(msg.sid);
        else if (msg && msg.type === 'read-aloud:notice') flashNotice(msg.text);
    });
}
