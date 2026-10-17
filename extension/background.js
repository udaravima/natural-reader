import { synthesizeBase64 } from './shared/api.js';
import { getSettings, getToken } from './shared/settings.js';
import { splitSentences, chunkSentences, extractSelectionOrPage } from './shared/text.js';

const MENU_SELECTION = 'read-aloud-selection';
const MENU_PAGE = 'read-aloud-page';
const SESSION_PREFIX = 'read:';

chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({ id: MENU_SELECTION, title: 'Read selection aloud', contexts: ['selection'] });
    chrome.contextMenus.create({ id: MENU_PAGE, title: 'Read whole page aloud', contexts: ['page'] });
});

// The text is read here, through an injected function, so no extension code is
// loaded into the page. The chunks wait in storage.session (it survives the
// worker being stopped) until the toolbar frame collects them by id.
async function startRead(tabId, mode) {
    const [first] = await chrome.scripting.executeScript({
        target: { tabId }, func: extractSelectionOrPage, args: [null, mode],
    });
    // Injecting an already-injected file is a no-op re-run guarded inside content.js.
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content/content.js'] });
    const sentences = splitSentences((first && first.result) || '');
    if (sentences.length === 0) {
        await chrome.tabs.sendMessage(tabId, { type: 'read-aloud:notice', text: 'Read Aloud: nothing to read' });
        return;
    }
    const chunks = mode === 'selection' ? [sentences] : chunkSentences(sentences, 30);
    const sid = crypto.randomUUID();
    await chrome.storage.session.set({ [SESSION_PREFIX + sid]: { chunks } });
    await chrome.tabs.sendMessage(tabId, { type: 'read-aloud:open', sid });
}

async function takeSession(sid) {
    const key = SESSION_PREFIX + sid;
    const found = await chrome.storage.session.get(key);
    await chrome.storage.session.remove(key);
    return found[key] || null;
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (!tab || tab.id == null) return;
    if (info.menuItemId === MENU_SELECTION) startRead(tab.id, 'selection').catch((e) => console.error('[read-aloud]', e));
    else if (info.menuItemId === MENU_PAGE) startRead(tab.id, 'page').catch((e) => console.error('[read-aloud]', e));
});

// Synthesis for the toolbar. It runs here: the worker's host permission skips
// the CORS check, and the token never reaches the page. Only voice, speed and
// the sentences come from the caller; the URL and token are read from storage.
async function synthesizeForToolbar({ sentences, voice, speed }) {
    const { baseUrl } = await getSettings();
    const token = await getToken();
    try {
        return await synthesizeBase64({ sentences, voice, speed, baseUrl, token });
    } catch (e) {
        if (e instanceof TypeError) { // fetch itself failed: no answer at all
            throw new Error(`can't reach ${baseUrl}. Is the server running, and has the `
                + 'extension been allowed to reach it? (Settings → Test connection)');
        }
        throw e;
    }
}

// Extension pages (the toolbar frame, the popup) as opposed to content scripts,
// whose sender.url is the web page's.
function fromExtensionPage(sender) {
    return sender.id === chrome.runtime.id && typeof sender.url === 'string'
        && sender.url.startsWith(chrome.runtime.getURL(''));
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || !fromExtensionPage(sender)) return undefined;
    if (msg.type === 'read-aloud:synthesize') {
        synthesizeForToolbar(msg)
            .then((audio) => sendResponse({ ok: true, audio }))
            .catch((e) => sendResponse({ ok: false, error: e && e.message ? e.message : String(e) }));
        return true;
    }
    if (msg.type === 'read-aloud:session') {
        takeSession(String(msg.sid || '')).then(sendResponse, () => sendResponse(null));
        return true;
    }
    if (msg.type === 'read-aloud:popup-read' && msg.tabId != null) {
        startRead(msg.tabId, msg.mode)
            .then(() => sendResponse({ ok: true }))
            .catch((e) => sendResponse({ ok: false, error: String(e) }));
        return true; // keep the message channel open for the async response
    }
    return undefined;
});
