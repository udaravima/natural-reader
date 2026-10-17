# Natural Reader Chrome Extension

Natural Reader is a Chrome extension that reads selected text or entire web pages aloud using your Natural Reader server's Kokoro text-to-speech. Right-click on any selection to play it immediately, or use the context menu to read the whole page with chunked playback, seeking, and voice/speed control. The extension includes an options page for customizing voice, playback speed, and backend URL.

## Install (Unpacked)

1. Open Chrome and navigate to `chrome://extensions`
2. Enable **Developer mode** (toggle in the top right)
3. Click **Load unpacked**
4. Navigate to the `extension/` directory in this repo and select it
5. The extension will appear in your extensions list; pin it to the toolbar for easy access

## Requirement: a Natural Reader server

The extension needs a running Natural Reader server. For one on this machine:

```bash
cd /path/to/natural-reader
./startup.sh up
```

You can verify the backend is running by checking its health endpoint:

```bash
curl http://localhost:8000/v1/health
```

The popup's status dot is green when a read would work, amber when the server
answers but refuses you (no token, a wrong or revoked token, or an account
without the `reader` permission), and red when the server can't be reached.
The line under the title says which.

## Sign-in: the access token

A server with sign-in on (the default) rejects reads without a token, so the
toolbar turns red with *"not signed in"*. To connect:

1. In Natural Reader, open **Settings → Account**. Leave **Access** at
   **Read aloud** only (the default), pick an expiry, type a name (e.g.
   `laptop-extension`) and click **Create**. Copy the `nrp_…` token: it's
   shown only once. A read-aloud-only token can't open your documents, chats
   or the admin console, so it costs little if it leaks.
2. Right-click the Read Aloud icon → **Options**, paste it into **Access
   token**, and click **Test connection**. It should say *Connected as* your
   email.

The token is kept in `chrome.storage.local` on this computer only (not Chrome
sync), and only the extension's service worker sends it. Web pages never see
it. Revoke it in **Settings → Account** to cut the extension off. A
single-user server (`AUTH_ENABLED=false`) needs no token.

**A server that isn't on `localhost`** (e.g. `https://reader.example.com`):
enter its URL as **Backend URL**, then click **Test connection**. Chrome asks
whether the extension may reach that site; allow it, or every read fails with
*"can't reach…"*.

## Usage

- **Read Selection:** Select text → right-click → **"Read selection aloud"** (toolbar pops up with playback controls; audio plays immediately)
- **Read Whole Page:** Right-click on the page → **"Read whole page aloud"** (text is chunked automatically; a toolbar shows the progress, e.g., "chunk 3 / 10")
- **Playback Controls:** Play/Pause, Stop, and seek bar in the toolbar
- **Voice & Speed:** Click the extension popup → **Options** or right-click the extension icon → **Options page**
  - Choose a voice, adjust playback speed, change the backend URL, and paste an access token
  - Click **Test connection** to check the server is reachable and accepts the token
- **Close:** Click the ✕ button on the toolbar to dismiss the audio player

## Limitations (v0.2)

- **Highlight follow:** no visual highlight of words as they are read
- **Page extraction:** basic extraction; some complex layouts or dynamic content may not extract correctly
- **Navigation:** audio stops if you navigate away from the current page

## How it fits together

| Piece | Runs in | Does |
|---|---|---|
| `background.js` | the service worker | reads the page's text with an injected function, splits it into chunks, holds the token, calls the server |
| `content/content.js` | the page (isolated world) | places the toolbar frame, moves it while you drag, shows the "nothing to read" notice |
| `toolbar/` | an extension page in an iframe | the controls, the playback queue and the audio |

None of the extension's code or audio is loaded into the page itself. That's
why it works on sites with a strict Content-Security-Policy (GitHub,
`claude.ai`): such a policy blocks the `blob:` audio a player in the page
needs. Up to 0.1.0 the toolbar played audio in the page, so on those sites it
showed "reading" and stayed silent. The page's policy decides what the page
may load; the toolbar frame plays under the extension's own policy.

## Site compatibility (known)

The extension works on ordinary web pages and on strict-CSP sites. It can
still fail on:

- **Sandboxed iframes / embedded content** (e.g. a rendered artifact inside a host
  page): the content script runs only in the top page, so text living inside a
  sandboxed iframe isn't seen — a "read whole page" there may report *"nothing to
  read."*

## Known rough edges (deferred)

- Stopping/closing doesn't abort an in-flight synthesis request server-side (the
  audio is just discarded client-side).
- Rapid double-triggers (e.g. selection-read while a page-read is starting) can
  briefly spawn a second toolbar.
- Starting a read in a second tab doesn't stop the first; each tab has its own toolbar.

## Manual E2E Checklist

- [ ] Load unpacked; no manifest/service-worker errors.
- [ ] Backend up, sign-in on, no token: popup dot amber, "Sign-in needed"; a read shows "not signed in" in red.
- [ ] Token pasted → Test connection says "Connected as <email>"; popup dot green.
- [ ] Revoke the token in the app → popup says "Token rejected", dot amber.
- [ ] Non-localhost Backend URL → Test connection prompts for site access; after allowing, reads work.
- [ ] Selection → context menu → toolbar + audio plays.
- [ ] Whole page → chunked queue; "chunk N / M"; starts after first chunk.
- [ ] On github.com (strict CSP), both reads play audio, not a silent "reading".
- [ ] Play/Pause, Stop, seek, drag, close all work.
- [ ] Voice/speed change mid-read applies to a later chunk (not the one already being fetched).
- [ ] Backend down → toolbar shows error (red), popup dot red, no hang.
- [ ] Empty selection/page → "nothing to read" notice.
- [ ] Options: change voice/speed/URL persists; Test connection ✓ / ✗.
