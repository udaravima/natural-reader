# SPA Auth UI + Local OIDC Rig Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the React SPA into the already-built multi-user auth backend — a login gate, pending/disabled screens, a PAT panel, and an admin user-management panel — plus a reproducible local Keycloak/proxy rig to exercise the real OIDC flow.

**Architecture:** All backend calls flow through a single new `apiFetch` seam that sends the session cookie and, on any 401, drops the app back to the login gate. A `useAuth` hook probes `/v1/auth/me` on load and drives a five-state machine (`loading | anonymous | pending | disabled | active`); an `AuthGate` renders the matching screen or the app. Cookie auth requires the SPA and backend to be same-origin, provided by a Vite dev proxy (dev) and the user's existing system nginx (prod-like). PATs (Bearer) cover the one client that can't be same-origin — the extension.

**Tech Stack:** React 19, Vite (Rolldown), Vitest + @testing-library/react (jsdom), FastAPI/pytest (backend), Authlib OIDC RP, Keycloak (podman container), system nginx.

**Spec:** [docs/superpowers/specs/2026-09-16-spa-auth-ui-design.md](../specs/2026-09-16-spa-auth-ui-design.md)

## Global Constraints

- **Same-origin is load-bearing.** Cookie auth only works when the browser sees SPA + backend as one origin. Never assume a cross-origin `apiHost` works for cookies; that path needs `FRONTEND_ORIGIN` pinned (already supported).
- **Only `/v1` calls migrate to `apiFetch`.** The `/api/*` calls go browser→Ollama directly and must stay bare (no credentials, no auth). Do not touch them.
- **`apiFetch` always sends `credentials: 'include'`** and merges caller options over it.
- **The backend cookie is already correct** (`nr_session`, httpOnly + Secure + SameSite=Lax, TTL 168h). Do not change cookie flags.
- **First-user-admin semantics:** the first person to ever log in becomes admin **regardless of email**; only the **second+** user is `pending`. Any pending-path test needs two distinct users.
- **403 contract:** a non-active authenticated user gets `403` with body `{"detail": {"status": "pending"|"disabled", "message": ...}}`. The SPA branches on `detail.status`.
- **No React Router.** Gating is conditional render (`AuthGate`), matching the single-`App.jsx` structure.
- **Settings is a collapsible Sidebar section, not a modal.** New Account/Admin panels follow the same collapsible pattern in `src/components/Sidebar.jsx`.
- **Keycloak on host port `18080`** (never its default 8080). **Do not edit system nginx files** — ship a reference `server` block only.
- **TDD throughout.** Backend: pytest (needs Postgres up — `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres`). Frontend: `npm run test:run`. Commit after each green task.

---

### Task 1: Backend — structured `status` on the 403

**Files:**
- Modify: `server/auth/deps.py:53-54`
- Test: `server/tests/test_auth_deps.py`

**Interfaces:**
- Produces: the 403 response contract `{"detail": {"status": <str>, "message": <str>}}` that `useAuth` (Task 3) consumes.

- [ ] **Step 1: Extend the existing pending test to assert the structured status**

In `server/tests/test_auth_deps.py`, replace `test_pending_user_is_403` with:

```python
async def test_pending_user_is_403_with_structured_status(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    _, raw = await create_token(db_conn, u2["id"], "cli")
    r = await _get(_app(db_conn), "/whoami", {"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
    assert r.json()["detail"]["status"] == "pending"


async def test_disabled_user_403_status_is_disabled(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await set_status(db_conn, u2["id"], "active")
    await set_status(db_conn, u2["id"], "disabled")
    _, raw = await create_token(db_conn, u2["id"], "cli")
    r = await _get(_app(db_conn), "/whoami", {"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
    assert r.json()["detail"]["status"] == "disabled"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_auth_deps.py -q`
Expected: FAIL — `detail` is currently the string `"Account pending"`, so `r.json()["detail"]["status"]` raises `TypeError: string indices must be integers`.

- [ ] **Step 3: Make the 403 detail structured**

In `server/auth/deps.py`, change the not-active branch:

```python
    if row["status"] != "active":
        raise HTTPException(
            status_code=403,
            detail={"status": row["status"], "message": f"Account {row['status']}"},
        )
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_auth_deps.py -q`
Expected: PASS. Then run the full auth slice to catch consumers that assumed a string detail:
Run: `.venv/bin/python -m pytest server/tests/ -q`
Expected: PASS (91+ tests). If any test asserted `detail == "Account pending"`, update it to `detail["message"] == "Account pending"`.

- [ ] **Step 5: Commit**

```bash
git add server/auth/deps.py server/tests/test_auth_deps.py
git commit -m "feat(auth): structured status on the not-active 403"
```

---

### Task 2: `apiFetch` credential seam

**Files:**
- Create: `src/utils/apiFetch.js`
- Test: `src/utils/apiFetch.test.js`

**Interfaces:**
- Consumes: `buildApiUrl(host, port, path)` from `src/utils/url.js`.
- Produces:
  - `apiFetch(host, port, path, opts = {}) → Promise<Response>` — adds `credentials:'include'`, merges `opts`, calls the registered handler on 401.
  - `setUnauthorizedHandler(fn | null)` — registers the global 401 callback.

- [ ] **Step 1: Write the failing tests**

Create `src/utils/apiFetch.test.js`:

```javascript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiFetch, setUnauthorizedHandler } from './apiFetch';

describe('apiFetch', () => {
  beforeEach(() => { global.fetch = vi.fn(); setUnauthorizedHandler(null); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('sends credentials:include and builds a same-origin URL when host is blank', async () => {
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await apiFetch('', '', '/v1/auth/me');
    expect(global.fetch).toHaveBeenCalledWith(
      '/v1/auth/me',
      expect.objectContaining({ credentials: 'include' }),
    );
  });

  it('builds an absolute URL from host+port and preserves method/body', async () => {
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await apiFetch('localhost', '8000', '/v1/docs', { method: 'POST', body: 'x' });
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/v1/docs',
      expect.objectContaining({ credentials: 'include', method: 'POST', body: 'x' }),
    );
  });

  it('invokes the unauthorized handler on 401 and still returns the response', async () => {
    global.fetch.mockResolvedValue(new Response('', { status: 401 }));
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    const res = await apiFetch('', '', '/v1/docs');
    expect(onUnauth).toHaveBeenCalledOnce();
    expect(res.status).toBe(401);
  });

  it('does NOT invoke the handler on a 200', async () => {
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    await apiFetch('', '', '/v1/docs');
    expect(onUnauth).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `npm run test:run -- src/utils/apiFetch.test.js`
Expected: FAIL — `Failed to resolve import './apiFetch'`.

- [ ] **Step 3: Implement**

Create `src/utils/apiFetch.js`:

```javascript
import { buildApiUrl } from './url';

// A single process-wide handler so any 401 — from any call site — can drop the
// app back to the login gate without threading a callback through every caller.
let _onUnauthorized = null;

export function setUnauthorizedHandler(fn) {
  _onUnauthorized = fn;
}

/**
 * Fetch a backend `/v1` endpoint with the session cookie attached.
 *
 * @param {string} host  apiHost setting ('' = same origin, behind the proxy)
 * @param {string} port  apiPort setting (ignored when host is blank)
 * @param {string} path  e.g. '/v1/auth/me'
 * @param {RequestInit} opts  merged over the defaults
 */
export async function apiFetch(host, port, path, opts = {}) {
  const res = await fetch(buildApiUrl(host, port, path), {
    credentials: 'include',
    ...opts,
  });
  if (res.status === 401 && _onUnauthorized) _onUnauthorized();
  return res;
}
```

- [ ] **Step 4: Run — verify pass**

Run: `npm run test:run -- src/utils/apiFetch.test.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/utils/apiFetch.js src/utils/apiFetch.test.js
git commit -m "feat(auth): apiFetch credential seam with global 401 handler"
```

---

### Task 3: `useAuth` hook — the state machine

**Files:**
- Create: `src/hooks/useAuth.js`
- Test: `src/hooks/useAuth.test.jsx`

**Interfaces:**
- Consumes: `apiFetch`, `setUnauthorizedHandler` (Task 2); `buildApiUrl` (Task 2's dep); the 403 `detail.status` contract (Task 1).
- Produces: `useAuth(apiHost, apiPort) → { state, user, login, logout, refresh }` where `state ∈ {'loading','anonymous','pending','disabled','active','error'}` and `user` is `{id,email,role}|null`.

- [ ] **Step 1: Write the failing tests**

Create `src/hooks/useAuth.test.jsx`:

```javascript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useAuth } from './useAuth';

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('useAuth', () => {
  beforeEach(() => { global.fetch = vi.fn(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('resolves to active with the user on a 200 /me', async () => {
    global.fetch.mockResolvedValue(jsonResponse(200, { id: '1', email: 'a@x.io', role: 'admin' }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('active'));
    expect(result.current.user).toEqual({ id: '1', email: 'a@x.io', role: 'admin' });
  });

  it('resolves to anonymous on 401', async () => {
    global.fetch.mockResolvedValue(new Response('', { status: 401 }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('anonymous'));
  });

  it('resolves to pending on a 403 with detail.status=pending', async () => {
    global.fetch.mockResolvedValue(jsonResponse(403, { detail: { status: 'pending' } }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('pending'));
  });

  it('resolves to disabled on a 403 with detail.status=disabled', async () => {
    global.fetch.mockResolvedValue(jsonResponse(403, { detail: { status: 'disabled' } }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('disabled'));
  });

  it('resolves to error when the request throws', async () => {
    global.fetch.mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('error'));
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `npm run test:run -- src/hooks/useAuth.test.jsx`
Expected: FAIL — cannot resolve `./useAuth`.

- [ ] **Step 3: Implement**

Create `src/hooks/useAuth.js`:

```javascript
import { useCallback, useEffect, useState } from 'react';
import { apiFetch, setUnauthorizedHandler } from '../utils/apiFetch';
import { buildApiUrl } from '../utils/url';

export function useAuth(apiHost, apiPort) {
  const [state, setState] = useState('loading');
  const [user, setUser] = useState(null);

  const check = useCallback(async () => {
    setState('loading');
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/auth/me');
      if (res.ok) {
        setUser(await res.json());
        setState('active');
      } else if (res.status === 401) {
        setUser(null);
        setState('anonymous');
      } else if (res.status === 403) {
        const body = await res.json().catch(() => ({}));
        setState(body?.detail?.status === 'disabled' ? 'disabled' : 'pending');
      } else {
        setState('error');
      }
    } catch {
      setState('error');
    }
  }, [apiHost, apiPort]);

  // Any 401 from any call site drops us back to the login gate.
  useEffect(() => {
    setUnauthorizedHandler(() => { setUser(null); setState('anonymous'); });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => { check(); }, [check]);

  const login = useCallback(() => {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.assign(buildApiUrl(apiHost, apiPort, `/v1/auth/login?next=${next}`));
  }, [apiHost, apiPort]);

  const logout = useCallback(async () => {
    try { await apiFetch(apiHost, apiPort, '/v1/auth/logout', { method: 'POST' }); }
    finally { setUser(null); setState('anonymous'); }
  }, [apiHost, apiPort]);

  return { state, user, login, logout, refresh: check };
}
```

- [ ] **Step 4: Run — verify pass**

Run: `npm run test:run -- src/hooks/useAuth.test.jsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hooks/useAuth.js src/hooks/useAuth.test.jsx
git commit -m "feat(auth): useAuth state machine (me probe + login/logout/refresh)"
```

---

### Task 4: `AuthGate` + gate screens

**Files:**
- Create: `src/components/auth/AuthGate.jsx` (AuthGate + the four screens in one file — small presentational units that change together)
- Test: `src/components/auth/AuthGate.test.jsx`

**Interfaces:**
- Consumes: nothing beyond props.
- Produces: `<AuthGate state onLogin onLogout onRetry>{children}</AuthGate>` — renders `children` only when `state === 'active'`, otherwise the matching screen.

- [ ] **Step 1: Write the failing tests**

Create `src/components/auth/AuthGate.test.jsx`:

```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AuthGate } from './AuthGate';

const child = <div data-testid="app">APP</div>;

describe('AuthGate', () => {
  it('renders children only when active', () => {
    render(<AuthGate state="active">{child}</AuthGate>);
    expect(screen.getByTestId('app')).toBeInTheDocument();
  });

  it('shows the login screen and fires onLogin when anonymous', () => {
    const onLogin = vi.fn();
    render(<AuthGate state="anonymous" onLogin={onLogin}>{child}</AuthGate>);
    expect(screen.queryByTestId('app')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(onLogin).toHaveBeenCalledOnce();
  });

  it('shows the awaiting-approval screen when pending', () => {
    render(<AuthGate state="pending" onLogout={vi.fn()}>{child}</AuthGate>);
    expect(screen.getByText(/awaiting approval/i)).toBeInTheDocument();
    expect(screen.queryByTestId('app')).toBeNull();
  });

  it('shows the disabled screen when disabled', () => {
    render(<AuthGate state="disabled" onLogout={vi.fn()}>{child}</AuthGate>);
    expect(screen.getByText(/disabled/i)).toBeInTheDocument();
  });

  it('shows a retry on error', () => {
    const onRetry = vi.fn();
    render(<AuthGate state="error" onRetry={onRetry}>{child}</AuthGate>);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `npm run test:run -- src/components/auth/AuthGate.test.jsx`
Expected: FAIL — cannot resolve `./AuthGate`.

- [ ] **Step 3: Implement**

Create `src/components/auth/AuthGate.jsx`:

```javascript
// Full-page auth gate. No router — App wraps its render in this and only the
// `active` state mounts the app. Screens are intentionally minimal; they inherit
// the page's default styling and are replaced/skinned later if needed.
function Screen({ title, children }) {
  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', flexDirection: 'column', gap: '1rem', padding: '2rem',
      textAlign: 'center', fontFamily: 'system-ui, sans-serif',
    }}>
      <h1 style={{ fontSize: '1.25rem', margin: 0 }}>{title}</h1>
      {children}
    </div>
  );
}

const btn = {
  padding: '0.6rem 1.2rem', borderRadius: '0.5rem', border: '1px solid #3b82f6',
  background: '#3b82f6', color: '#fff', fontWeight: 700, cursor: 'pointer',
};

function LoginScreen({ onLogin }) {
  return (
    <Screen title="Natural Reader">
      <p>Sign in to continue.</p>
      <button style={btn} onClick={onLogin}>Sign in</button>
    </Screen>
  );
}

function PendingScreen({ onLogout }) {
  return (
    <Screen title="Your account is awaiting approval">
      <p>An administrator needs to activate your account. Check back shortly.</p>
      <div style={{ display: 'flex', gap: '0.75rem' }}>
        <button style={btn} onClick={() => window.location.reload()}>Refresh</button>
        <button style={{ ...btn, background: 'transparent', color: '#3b82f6' }} onClick={onLogout}>Log out</button>
      </div>
    </Screen>
  );
}

function DisabledScreen({ onLogout }) {
  return (
    <Screen title="Your access has been disabled">
      <p>Contact an administrator if you think this is a mistake.</p>
      <button style={btn} onClick={onLogout}>Log out</button>
    </Screen>
  );
}

function AuthErrorScreen({ onRetry }) {
  return (
    <Screen title="Can't reach the server">
      <p>The backend isn't responding.</p>
      <button style={btn} onClick={onRetry}>Retry</button>
    </Screen>
  );
}

export function AuthGate({ state, onLogin, onLogout, onRetry, children }) {
  switch (state) {
    case 'active': return <>{children}</>;
    case 'anonymous': return <LoginScreen onLogin={onLogin} />;
    case 'pending': return <PendingScreen onLogout={onLogout} />;
    case 'disabled': return <DisabledScreen onLogout={onLogout} />;
    case 'error': return <AuthErrorScreen onRetry={onRetry} />;
    default: return <Screen title="Loading…" />;
  }
}
```

- [ ] **Step 4: Run — verify pass**

Run: `npm run test:run -- src/components/auth/AuthGate.test.jsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/components/auth/AuthGate.jsx src/components/auth/AuthGate.test.jsx
git commit -m "feat(auth): AuthGate + login/pending/disabled/error screens"
```

---

### Task 5: Migrate every `/v1` fetch to `apiFetch`

Mechanical but load-bearing: a missed site sends no cookie → a spurious 401 → surprise logout. The seam makes a miss *loud*, but do a final grep audit.

**Files:**
- Modify: `src/App.jsx` (sites at lines ~268, 601, 652, 694, 721, 739, 786, 827, 861, 918, 961)
- Modify: `src/hooks/useTtsEngine.js` (`/v1/synthesize` ~62; `/v1/batch_synthesize` ~568, ~667)
- Modify: `src/lib/uploadPdf.js` (`/v1/docs/{id}/pdf` ~28)
- Modify: `src/lib/sessionStore.js` (`buildApiUrl(apiHost, apiPort, path)` sites)
- Modify: `src/lib/chatTools/searchDocument.js`, `src/lib/chatTools/webSearch.js`, `src/lib/chatTools/currentTimeDate.js` (`/v1/...` sites)

**Interfaces:**
- Consumes: `apiFetch(host, port, path, opts)` (Task 2).

- [ ] **Step 1: Import and transform each `/v1` call**

The transform in every file: replace `fetch(buildApiUrl(host, port, path), opts)` (or the local `getApiUrl`/`apiUrl` closures) with `apiFetch(host, port, path, opts)`, and add `import { apiFetch } from '<rel>/utils/apiFetch'`. Leave every `/api/*` (Ollama) call **unchanged**.

Worked example — `src/App.jsx` health probe (~268):

```javascript
// before
const response = await fetch(getApiUrl('/v1/health'), { method: 'GET', signal: controller.signal });
// after
const response = await apiFetch(apiHost, apiPort, '/v1/health', { method: 'GET', signal: controller.signal });
```

Worked example — `src/App.jsx` doc register (~652), preserving the local `apiUrl` closure's host/port:

```javascript
// before
registerRes = await fetch(apiUrl('/v1/docs'), { method: 'POST', headers: {...}, body: JSON.stringify(payload) });
// after
registerRes = await apiFetch(apiHost, apiPort, '/v1/docs', { method: 'POST', headers: {...}, body: JSON.stringify(payload) });
```

Worked example — `src/hooks/useTtsEngine.js` (~62), which already has `apiHost`/`apiPort` in scope:

```javascript
// before
const url = buildApiUrl(apiHost, apiPort, '/v1/synthesize');
const response = await fetch(url, { method: 'POST', headers: {...}, body });
// after
const response = await apiFetch(apiHost, apiPort, '/v1/synthesize', { method: 'POST', headers: {...}, body });
```

For `src/lib/*` helpers that receive `apiHost`/`apiPort` (or `apiHost, apiPort` via an options object), pass those same values into `apiFetch`. Do **not** change their function signatures — they already carry host/port.

- [ ] **Step 2: Grep-audit for stragglers**

Run: `grep -rnE "fetch\(.*/v1/|buildApiUrl\([^)]*/v1/" src/ | grep -v apiFetch | grep -v '\.test\.'`
Expected: no lines. Any remaining `/v1` `fetch`/`buildApiUrl` that isn't `apiFetch` is a straggler — convert it. (Confirm `/api/*` Ollama calls are untouched: `grep -rn "/api/" src/hooks/useChatEngine.js | head` should still show bare `fetch`.)

- [ ] **Step 3: Run the full frontend suite**

Run: `npm run test:run`
Expected: PASS. Existing tests that stub `global.fetch` still work (apiFetch calls `fetch` under the hood); if a test asserted an exact URL string that now goes through `buildApiUrl` unchanged, it still matches. Fix any test that asserted call *options* to include `credentials: 'include'`.

- [ ] **Step 4: Commit**

```bash
git add src/App.jsx src/hooks/useTtsEngine.js src/lib/
git commit -m "refactor(auth): route all /v1 fetches through apiFetch (credentials)"
```

---

### Task 6: Wire `useAuth` + `AuthGate` into `App.jsx`

**Files:**
- Modify: `src/App.jsx` (add the hook near the other hooks; wrap the top-level return)

**Interfaces:**
- Consumes: `useAuth` (Task 3), `AuthGate` (Task 4).

- [ ] **Step 1: Add the hook and imports**

Near the top of `App.jsx` with the other imports:

```javascript
import { useAuth } from './hooks/useAuth';
import { AuthGate } from './components/auth/AuthGate';
```

With the other hooks (after `apiHost`/`apiPort` are declared, ~line 53):

```javascript
const auth = useAuth(apiHost, apiPort);
```

- [ ] **Step 2: Wrap the top-level return**

Find the component's main `return (` and wrap its top-level element:

```javascript
return (
  <AuthGate
    state={auth.state}
    onLogin={auth.login}
    onLogout={auth.logout}
    onRetry={auth.refresh}
  >
    {/* the existing top-level JSX, unchanged */}
  </AuthGate>
);
```

Because `children` is only mounted in the `active` branch, none of the app's subtree renders for an unauthenticated visitor. (App's own hooks still run — harmless; they fail closed behind the gate.) Expose `auth.user` where the sidebar needs it (Tasks 7–8) by passing `user={auth.user}` and `onLogout={auth.logout}` to the `<Sidebar .../>` element.

- [ ] **Step 3: Manual verification via the dev bypass**

With `AUTH_ENABLED=false` and a loopback backend (`HOST=127.0.0.1`), `/v1/auth/me` returns the seed admin → gate resolves to `active` → the app renders exactly as before.

Run backend: `AUTH_ENABLED=false HOST=127.0.0.1 .venv/bin/python run.py` (separate shell)
Run: `npm run dev`, open the app.
Expected: the app renders normally (gate transparent). Temporarily hard-code `state="anonymous"` in a scratch render to eyeball the LoginScreen, then revert. Do **not** commit the scratch edit.

- [ ] **Step 4: Run the frontend suite**

Run: `npm run test:run`
Expected: PASS (no regressions; App-level wiring is covered by manual check + the unit tests of the pieces).

- [ ] **Step 5: Commit**

```bash
git add src/App.jsx
git commit -m "feat(auth): gate the app behind useAuth/AuthGate"
```

---

### Task 7: Account panel (PAT management)

**Files:**
- Create: `src/components/account/AccountPanel.jsx`
- Test: `src/components/account/AccountPanel.test.jsx`
- Modify: `src/components/Sidebar.jsx` (render the panel as a new collapsible section)

**Interfaces:**
- Consumes: `apiFetch` (Task 2); backend `/v1/auth/tokens` (GET/POST/DELETE); props `{ theme, apiHost, apiPort, user, onLogout }`.
- Produces: a self-contained panel that fetches/creates/revokes the current user's PATs.

- [ ] **Step 1: Write the failing tests**

Create `src/components/account/AccountPanel.test.jsx`:

```javascript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AccountPanel } from './AccountPanel';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const json = (status, body) => new Response(JSON.stringify(body), { status });
const baseProps = (over = {}) => ({ theme, apiHost: '', apiPort: '', user: { email: 'a@x.io', role: 'admin' }, onLogout: vi.fn(), ...over });

describe('AccountPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists existing tokens on mount', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 't1', name: 'cli', last_used_at: null, expires_at: null }]));
    render(<AccountPanel {...baseProps()} />);
    expect(await screen.findByText('cli')).toBeInTheDocument();
  });

  it('shows the raw token exactly once after create', async () => {
    apiFetch.mockResolvedValueOnce(json(200, []));                       // initial list
    apiFetch.mockResolvedValueOnce(json(200, { id: 't2', token: 'nrp_secret' })); // create
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 't2', name: 'ext', last_used_at: null, expires_at: null }])); // reload
    render(<AccountPanel {...baseProps()} />);
    fireEvent.change(await screen.findByPlaceholderText(/token name/i), { target: { value: 'ext' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    expect(await screen.findByText('nrp_secret')).toBeInTheDocument();
  });

  it('revokes a token via DELETE', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 't1', name: 'cli', last_used_at: null, expires_at: null }]));
    apiFetch.mockResolvedValueOnce(new Response('', { status: 204 })); // delete
    apiFetch.mockResolvedValueOnce(json(200, []));                     // reload
    render(<AccountPanel {...baseProps()} />);
    fireEvent.click(await screen.findByRole('button', { name: /revoke/i }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/auth/tokens/t1', expect.objectContaining({ method: 'DELETE' })));
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `npm run test:run -- src/components/account/AccountPanel.test.jsx`
Expected: FAIL — cannot resolve `./AccountPanel`.

- [ ] **Step 3: Implement**

Create `src/components/account/AccountPanel.jsx`:

```javascript
import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

export function AccountPanel({ theme, apiHost, apiPort, user, onLogout }) {
  const [tokens, setTokens] = useState([]);
  const [name, setName] = useState('');
  const [freshToken, setFreshToken] = useState(null); // shown once
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    const res = await apiFetch(apiHost, apiPort, '/v1/auth/tokens');
    if (res.ok) setTokens(await res.json());
  }, [apiHost, apiPort]);

  useEffect(() => { load(); }, [load]);

  const create = async () => {
    setError(null);
    const res = await apiFetch(apiHost, apiPort, '/v1/auth/tokens', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (res.ok) { const body = await res.json(); setFreshToken(body.token); setName(''); load(); }
    else setError('Could not create token');
  };

  const revoke = async (id) => {
    await apiFetch(apiHost, apiPort, `/v1/auth/tokens/${encodeURIComponent(id)}`, { method: 'DELETE' });
    load();
  };

  return (
    <div className="px-4 pb-4 flex flex-col gap-3">
      <div className={`text-[10px] ${theme.textMuted}`}>{user?.email} · {user?.role}</div>

      {freshToken && (
        <div className={`p-2 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
          <p className={`text-[10px] ${theme.textSecondary}`}>Copy this token now — you won't see it again.</p>
          <code className={`text-xs break-all ${theme.text}`}>{freshToken}</code>
          <button className="ml-2 text-[10px] underline" onClick={() => navigator.clipboard?.writeText(freshToken)}>Copy</button>
          <button className="ml-2 text-[10px] underline" onClick={() => setFreshToken(null)}>Done</button>
        </div>
      )}

      <div className="flex gap-2">
        <input
          value={name} onChange={(e) => setName(e.target.value)} placeholder="Token name"
          className={`flex-1 text-xs p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text}`}
        />
        <button disabled={!name.trim()} onClick={create}
          className="px-3 py-2 rounded-lg border text-xs font-bold disabled:opacity-50">Create</button>
      </div>
      {error && <p className="text-[10px] text-red-500">{error}</p>}

      <ul className="flex flex-col gap-1">
        {tokens.map((t) => (
          <li key={t.id} className="flex items-center justify-between text-xs">
            <span className={theme.text}>{t.name}</span>
            <button onClick={() => revoke(t.id)} className="text-[10px] text-red-500 underline">Revoke</button>
          </li>
        ))}
      </ul>

      <button onClick={onLogout} className={`text-[10px] ${theme.textMuted} underline self-start`}>Log out</button>
    </div>
  );
}
```

- [ ] **Step 4: Run — verify pass**

Run: `npm run test:run -- src/components/account/AccountPanel.test.jsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire into the Sidebar**

In `src/components/Sidebar.jsx`: `import { AccountPanel } from './account/AccountPanel'`, add `user` and `onLogout` to the destructured props, add `const [accountOpen, setAccountOpen] = useState(false)` with the other state, and add a collapsible section next to the existing Settings section — same markup pattern as the Settings toggle at `Sidebar.jsx:60-72` (an `<Icon/>` from `lucide-react` such as `User`, the toggle button, chevron, and the `max-h` transition body):

```jsx
{/* Account */}
<div className={`border-b ${theme.borderSecondary}`}>
  <button
    onClick={() => setAccountOpen(v => !v)}
    className="w-full flex items-center justify-between p-4 cursor-pointer hover:opacity-80"
  >
    <div className="flex items-center gap-2">
      <User size={14} className={theme.textMuted} />
      <h3 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Account</h3>
    </div>
    {accountOpen ? <ChevronUp size={14} className={theme.textMuted} /> : <ChevronDown size={14} className={theme.textMuted} />}
  </button>
  <div className={`overflow-hidden transition-all duration-300 ${accountOpen ? 'max-h-[70vh] opacity-100' : 'max-h-0 opacity-0'}`}>
    <AccountPanel theme={theme} apiHost={apiHost} apiPort={apiPort} user={user} onLogout={onLogout} />
  </div>
</div>
```

Add `User` (and confirm `ChevronUp`/`ChevronDown`) to the existing `lucide-react` import. In `App.jsx`, pass the new props: `<Sidebar ... user={auth.user} onLogout={auth.logout} />`.

- [ ] **Step 6: Run the frontend suite**

Run: `npm run test:run`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/components/account/ src/components/Sidebar.jsx src/App.jsx
git commit -m "feat(auth): account panel — personal access token management"
```

---

### Task 8: Admin panel (user management)

**Files:**
- Create: `src/components/admin/AdminPanel.jsx`
- Test: `src/components/admin/AdminPanel.test.jsx`
- Modify: `src/components/Sidebar.jsx` (render admin-only)

**Interfaces:**
- Consumes: `apiFetch`; backend `/v1/admin/users` (GET), `/v1/admin/users/{id}` (PATCH `{status?, role?}`); props `{ theme, apiHost, apiPort, currentUserId }`.

- [ ] **Step 1: Write the failing tests**

Create `src/components/admin/AdminPanel.test.jsx`:

```javascript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AdminPanel } from './AdminPanel';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const json = (status, body) => new Response(JSON.stringify(body), { status });
const props = (over = {}) => ({ theme, apiHost: '', apiPort: '', currentUserId: 'me', ...over });

describe('AdminPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists users on mount', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'u2', email: 'b@x.io', role: 'member', status: 'pending' }]));
    render(<AdminPanel {...props()} />);
    expect(await screen.findByText('b@x.io')).toBeInTheDocument();
  });

  it('activates a pending user via PATCH', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'u2', email: 'b@x.io', role: 'member', status: 'pending' }]));
    apiFetch.mockResolvedValueOnce(json(200, { ok: true })); // patch
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'u2', email: 'b@x.io', role: 'member', status: 'active' }])); // reload
    render(<AdminPanel {...props()} />);
    fireEvent.click(await screen.findByRole('button', { name: /activate/i }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/admin/users/u2',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'active' }) })));
  });

  it('does not offer self-disable for the current admin', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'me', email: 'me@x.io', role: 'admin', status: 'active' }]));
    render(<AdminPanel {...props()} />);
    await screen.findByText('me@x.io');
    expect(screen.queryByRole('button', { name: /disable/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `npm run test:run -- src/components/admin/AdminPanel.test.jsx`
Expected: FAIL — cannot resolve `./AdminPanel`.

- [ ] **Step 3: Implement**

Create `src/components/admin/AdminPanel.jsx`:

```javascript
import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

export function AdminPanel({ theme, apiHost, apiPort, currentUserId }) {
  const [users, setUsers] = useState([]);

  const load = useCallback(async () => {
    const res = await apiFetch(apiHost, apiPort, '/v1/admin/users');
    if (res.ok) setUsers(await res.json());
  }, [apiHost, apiPort]);

  useEffect(() => { load(); }, [load]);

  const patch = async (id, body) => {
    await apiFetch(apiHost, apiPort, `/v1/admin/users/${encodeURIComponent(id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    load();
  };

  return (
    <div className="px-4 pb-4 flex flex-col gap-2">
      {users.map((u) => {
        const isSelf = u.id === currentUserId;
        return (
          <div key={u.id} className="flex items-center justify-between text-xs gap-2">
            <span className={`${theme.text} truncate`}>{u.email}</span>
            <span className={`text-[10px] ${theme.textMuted}`}>{u.role}/{u.status}</span>
            <span className="flex gap-1">
              {u.status !== 'active' && (
                <button onClick={() => patch(u.id, { status: 'active' })} className="text-[10px] text-green-600 underline">Activate</button>
              )}
              {u.status === 'active' && !isSelf && (
                <button onClick={() => patch(u.id, { status: 'disabled' })} className="text-[10px] text-red-500 underline">Disable</button>
              )}
              {!isSelf && (
                <button onClick={() => patch(u.id, { role: u.role === 'admin' ? 'member' : 'admin' })} className="text-[10px] underline">
                  {u.role === 'admin' ? 'Make member' : 'Make admin'}
                </button>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Run — verify pass**

Run: `npm run test:run -- src/components/admin/AdminPanel.test.jsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire into the Sidebar (admin-only)**

In `src/components/Sidebar.jsx`: `import { AdminPanel } from './admin/AdminPanel'`, add `const [adminOpen, setAdminOpen] = useState(false)`, and add a second collapsible section — identical wrapper to the Account section from Task 7 but gated on role and using a `Shield`/`Users` icon:

```jsx
{user?.role === 'admin' && (
  <div className={`border-b ${theme.borderSecondary}`}>
    <button onClick={() => setAdminOpen(v => !v)} className="w-full flex items-center justify-between p-4 cursor-pointer hover:opacity-80">
      <div className="flex items-center gap-2">
        <Shield size={14} className={theme.textMuted} />
        <h3 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Admin</h3>
      </div>
      {adminOpen ? <ChevronUp size={14} className={theme.textMuted} /> : <ChevronDown size={14} className={theme.textMuted} />}
    </button>
    <div className={`overflow-hidden transition-all duration-300 ${adminOpen ? 'max-h-[70vh] opacity-100' : 'max-h-0 opacity-0'}`}>
      <AdminPanel theme={theme} apiHost={apiHost} apiPort={apiPort} currentUserId={user.id} />
    </div>
  </div>
)}
```

Add `Shield` to the `lucide-react` import.

- [ ] **Step 6: Run the frontend suite**

Run: `npm run test:run`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/components/admin/ src/components/Sidebar.jsx
git commit -m "feat(auth): admin panel — user activate/disable/role"
```

---

### Task 9: Local OIDC test rig (Vite proxy + Keycloak + nginx reference + docs)

No unit tests — the deliverable is a runnable end-to-end environment, verified manually.

**Files:**
- Modify: `vite.config.js` (dev proxy)
- Modify: `docker-compose.yml` (keycloak service)
- Create: `deploy/keycloak/realm-export.json`
- Create: `deploy/nginx/natural-reader.conf`
- Create: `deploy/README.md`
- Modify: `.env.example` (documented local OIDC values)

- [ ] **Step 1: Vite dev proxy**

In `vite.config.js`, add to the `defineConfig({...})` object:

```javascript
  server: {
    proxy: {
      '/v1': 'http://localhost:8000',
      '/api': 'http://localhost:11434',
    },
  },
```

- [ ] **Step 2: Keycloak service (non-default port 18080)**

Add to `docker-compose.yml` under `services:`:

```yaml
  keycloak:
    image: quay.io/keycloak/keycloak:26.0
    command: ["start-dev", "--import-realm", "--http-port=8080"]
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
    ports:
      - "127.0.0.1:18080:8080"
    volumes:
      - ./deploy/keycloak:/opt/keycloak/data/import:ro
```

- [ ] **Step 3: Realm export**

Create `deploy/keycloak/realm-export.json` — realm `natural-reader`, a confidential client `natural-reader` (standard flow + PKCE S256), a verified test user, and redirect URIs for both the Vite and nginx origins:

```json
{
  "realm": "natural-reader",
  "enabled": true,
  "clients": [{
    "clientId": "natural-reader",
    "enabled": true,
    "protocol": "openid-connect",
    "publicClient": false,
    "secret": "natural-reader-dev-secret",
    "standardFlowEnabled": true,
    "redirectUris": [
      "http://localhost:5173/v1/auth/callback",
      "http://localhost:8080/v1/auth/callback"
    ],
    "webOrigins": ["http://localhost:5173", "http://localhost:8080"],
    "attributes": { "pkce.code.challenge.method": "S256" }
  }],
  "users": [{
    "username": "admin-user",
    "email": "admin@example.com",
    "emailVerified": true,
    "enabled": true,
    "firstName": "Admin",
    "lastName": "User",
    "credentials": [{ "type": "password", "value": "password", "temporary": false }]
  }]
}
```

- [ ] **Step 4: nginx reference site**

Create `deploy/nginx/natural-reader.conf` (a reference `server` block the user installs into their own nginx — this repo never touches system files):

```nginx
# Reference site for Natural Reader — SPA + same-origin backend proxy.
# Adjust server_name / listen / root to your host, then install into your nginx.
server {
    listen 8080;                      # your app origin (stand-in)
    server_name localhost;
    root /path/to/natural-reader/dist;   # `npm run build` output
    index index.html;

    location / { try_files $uri /index.html; }   # SPA fallback

    location /v1/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;             # SSE/streaming-friendly
    }
    location /api/ {                     # browser → Ollama (unchanged behavior)
        proxy_pass http://127.0.0.1:11434;
        proxy_set_header Host $host;
    }
}
```

- [ ] **Step 5: `.env.example` local OIDC values**

Under the existing Authentication block in `.env.example`, document the working local values (commented), matching the realm above: `OIDC_ISSUER=http://localhost:18080/realms/natural-reader`, `OIDC_CLIENT_ID=natural-reader`, `OIDC_CLIENT_SECRET=natural-reader-dev-secret`, `OIDC_REDIRECT_URL=http://localhost:5173/v1/auth/callback`, `SESSION_SECRET=<generate>`, `COOKIE_SECURE=false`, `BOOTSTRAP_ADMIN_EMAIL=admin@example.com`, and a note that `COOKIE_DOMAIN` stays unset for localhost.

- [ ] **Step 6: `deploy/README.md` — the end-to-end walk-through**

Create `deploy/README.md` documenting: bring up services (`env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres keycloak`); wait for Keycloak (`curl -s http://localhost:18080/realms/natural-reader/.well-known/openid-configuration | head`); start the backend with the OIDC env; `npm run dev`; then the **two-user** flow (§6/§10 of the spec): admin-user logs in → becomes admin; a second Keycloak user (add one in the console) logs in → pending screen → admin activates in the Admin panel → refresh → active → create a PAT → log out.

- [ ] **Step 7: Verify the rig**

Run: `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres keycloak`
Run: `curl -s http://localhost:18080/realms/natural-reader/.well-known/openid-configuration | grep -o '"issuer":"[^"]*"'`
Expected: `"issuer":"http://localhost:18080/realms/natural-reader"` — realm imported and discoverable.
Then walk the two-user flow from `deploy/README.md` against `npm run dev`.

- [ ] **Step 8: Commit**

```bash
git add vite.config.js docker-compose.yml deploy/ .env.example
git commit -m "feat(auth): local OIDC rig — vite proxy, keycloak, nginx ref, docs"
```

---

## Notes for the executor

- **Backend tests need Postgres up**; frontend tests don't. Restart Postgres each session (the container stops between sessions).
- **The dev bypass** (`AUTH_ENABLED=false` on a loopback bind) is the fast path for Tasks 5–8: the gate resolves to `active` without Keycloak, so you can build and eyeball the UI before the rig (Task 9) exists.
- **Commit policy:** the repo owner requires per-action approval for commits. Treat each task's commit step as "propose and wait" unless told otherwise.
