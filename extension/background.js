const MENU_SELECTION = 'read-aloud-selection';
const MENU_PAGE = 'read-aloud-page';

chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({ id: MENU_SELECTION, title: 'Read selection aloud', contexts: ['selection'] });
    chrome.contextMenus.create({ id: MENU_PAGE, title: 'Read whole page aloud', contexts: ['page'] });
});

async function startRead(tabId, mode) {
    // Injecting an already-injected file is a no-op re-run guarded inside content.js.
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content/content.js'] });
    await chrome.tabs.sendMessage(tabId, { type: 'read-aloud:start', mode });
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (!tab || tab.id == null) return;
    if (info.menuItemId === MENU_SELECTION) startRead(tab.id, 'selection').catch((e) => console.error('[read-aloud]', e));
    else if (info.menuItemId === MENU_PAGE) startRead(tab.id, 'page').catch((e) => console.error('[read-aloud]', e));
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === 'read-aloud:popup-read' && msg.tabId != null) {
        startRead(msg.tabId, msg.mode)
            .then(() => sendResponse({ ok: true }))
            .catch((e) => sendResponse({ ok: false, error: String(e) }));
        return true; // keep the message channel open for the async response
    }
    return undefined;
});
