# App Shell — Settings Page + Profile Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate every scattered app setting into a dedicated `viewMode: 'settings'` page and add a Header profile-icon menu (identity, dark-mode toggle, logout).

**Architecture:** A new full-page Settings view (like Library/Admin) plus a `ProfileMenu` dropdown in the persistent Header. We relocate the *controls* only — every setting is already App-level `usePersistedState`, so `SettingsPage` receives the same values + setters via grouped prop bags. No backend, no migration, no behavior change to what settings do.

**Tech Stack:** React 18 + Vite, Tailwind, lucide-react icons, Vitest + @testing-library/react. Persistence via `usePersistedState`.

**Spec:** [docs/superpowers/specs/2026-09-22-app-shell-settings-profile-design.md](../specs/2026-09-22-app-shell-settings-profile-design.md)

## Global Constraints

- **Branch:** `development` (never `master`). Commit after each task.
- **Frontend-only.** No backend, no migration, no new persistence keys — controls are relocated, state is unchanged.
- **Test/lint gates:** `npm run test:run` (full suite) and `npm run lint` must stay green. Run a single file with `npx vitest run <path>`.
- **Commit trailer (required):** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Pre-existing lint:** `useAuth.js:51` already reports `react-hooks/set-state-in-effect` — do not touch it; only ensure no *new* lint errors.
- **Pattern reuse:** dropdowns follow `src/components/HeaderOverflowMenu.jsx` / `PdfToolbarMenu.jsx` (open state + outside-click/Escape close).

## Shared interfaces (grouped prop bags)

`SettingsPage` and `ProfileMenu` receive these exact shapes (defined once here; wired in Task 7):

```
voiceSettings = {
  selectedVoice, setSelectedVoice, playbackSpeed, setPlaybackSpeed,
  volume, setVolume, isLocalhost, setIsLocalhost,      // voice backend Kokoro/System
  requestTimeout, setRequestTimeout,
  unlimitedBatchTimeout, setUnlimitedBatchTimeout,
  isPreviewingVoice, previewVoice, stopVoicePreview, clearCache,
}
chatSettings = {
  inferenceSource, setInferenceSource,
  chatTtsMode, setChatTtsMode, chatAutoTts, setChatAutoTts,
  inferenceByModel, setInferenceByModel, availableModels, selectedModel,
}
connectionSettings = {
  apiHost, setApiHost, apiPort, setApiPort,             // reader TTS + /v1 API + auth
  ollamaHost, setOllamaHost, ollamaPort, setOllamaPort, // chat local mode only
  inferenceSource,                                      // to gate the Ollama fields
}
appearanceSettings = {
  darkMode, setDarkMode, layoutMode, setLayoutMode,
  mobileBreakpoint, setMobileBreakpoint,
  showHeaderControlsOnMobile, setShowHeaderControlsOnMobile,
}
accountProps = { apiHost, apiPort, user }               // AccountPanel, no onLogout
```

Per-model inference read/write uses the pure helpers in
[src/hooks/inference.js](../../../src/hooks/inference.js): `resolveForModel(map, model)`
and `patchForModel(map, model, patch)`, plus `INFERENCE_DEFAULTS`.

---

### Task 1: Allow the `settings` view in the boot guard

**Files:**
- Modify: `src/hooks/useViewModeGuard.js` (the `CAP_FREE_VIEWS` array)
- Test: `src/hooks/useViewModeGuard.test.js`

**Interfaces:**
- Produces: `settings` recognised as a capability-free view (no coercion).

- [ ] **Step 1: Write the failing test** — add to `useViewModeGuard.test.js`:

```javascript
it('leaves the ungated Settings view alone (like Library)', () => {
  const setViewMode = vi.fn();
  renderHook(() => useViewModeGuard({
    viewMode: 'settings', setViewMode, authState: 'active', caps: ['reader'],
  }));
  expect(setViewMode).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/hooks/useViewModeGuard.test.js`
Expected: FAIL — current guard coerces `settings` to `reader`.

- [ ] **Step 3: Implement** — in `useViewModeGuard.js` change:

```javascript
const CAP_FREE_VIEWS = ['library', 'settings'];
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/hooks/useViewModeGuard.test.js`
Expected: PASS (all 11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hooks/useViewModeGuard.js src/hooks/useViewModeGuard.test.js
git commit -m "feat(ui): allow the settings view in the boot guard (CAP_FREE_VIEWS)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Extract `InferenceRow` into a shared component

`InferenceRow` is a private function in `ChatSidebar.jsx:434`; the Settings page needs it too. Move it to its own file, unchanged, and re-import in ChatSidebar (behavior identical).

**Files:**
- Create: `src/components/chat/InferenceRow.jsx`
- Modify: `src/components/ChatSidebar.jsx` (delete the local function, add the import)
- Test: `src/components/chat/InferenceRow.test.jsx`

**Interfaces:**
- Produces: `export function InferenceRow({ theme, label, value, onChange, options, disabled = false })` — `options` is an array of `[value, label]` pairs; renders a labeled `<select>` that calls `onChange(newValue)`.

- [ ] **Step 1: Write the failing test** — `src/components/chat/InferenceRow.test.jsx`:

```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { InferenceRow } from './InferenceRow';

const theme = { border: '', bgSecondary: '', text: '', textSecondary: '', textMuted: '' };

describe('InferenceRow', () => {
  it('renders the label and options and calls onChange with the picked value', () => {
    const onChange = vi.fn();
    render(<InferenceRow theme={theme} label="Context window" value="auto"
      onChange={onChange} options={[['auto', 'Auto'], ['4096', '4096']]} />);
    expect(screen.getByText('Context window')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '4096' } });
    expect(onChange).toHaveBeenCalledWith('4096');
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/chat/InferenceRow.test.jsx`
Expected: FAIL — module `./InferenceRow` does not exist.

- [ ] **Step 3: Implement** — create `src/components/chat/InferenceRow.jsx` by moving the function body currently at `ChatSidebar.jsx:434` verbatim, adding `export` and the `Sliders`-free imports it needs (it uses only `theme` + a `<select>`; copy any icon import it references). Then in `ChatSidebar.jsx`: delete the local `function InferenceRow(...)` and add `import { InferenceRow } from './chat/InferenceRow';` near the top.

- [ ] **Step 4: Run tests**

Run: `npx vitest run src/components/chat/InferenceRow.test.jsx src/components/ChatSidebar.inference.test.jsx`
Expected: PASS (InferenceRow + existing ChatSidebar inference tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/components/chat/InferenceRow.jsx src/components/chat/InferenceRow.test.jsx src/components/ChatSidebar.jsx
git commit -m "refactor(ui): extract InferenceRow into a shared component

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `ProfileMenu` component

**Files:**
- Create: `src/components/ProfileMenu.jsx`
- Test: `src/components/ProfileMenu.test.jsx`

**Interfaces:**
- Produces: `export default function ProfileMenu({ theme, user, darkMode, setDarkMode, setViewMode, onLogout })` — a profile-icon button opening a dropdown with the user's identity, a **Settings** item (`setViewMode('settings')`), a **dark mode** toggle (`setDarkMode`), and **Log out** (`onLogout`). Outside-click/Escape close, mirroring `PdfToolbarMenu`.

- [ ] **Step 1: Write the failing test** — `src/components/ProfileMenu.test.jsx`:

```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ProfileMenu from './ProfileMenu';

const theme = { border: '', bgSecondary: '', bgTertiary: '', hover: '', text: '', textSecondary: '', textMuted: '' };
const base = (over = {}) => ({
  theme, user: { email: 'me@x.io', display_name: 'Me' },
  darkMode: false, setDarkMode: vi.fn(), setViewMode: vi.fn(), onLogout: vi.fn(), ...over,
});

describe('ProfileMenu', () => {
  it('opens and shows the identity', () => {
    render(<ProfileMenu {...base()} />);
    fireEvent.click(screen.getByRole('button', { name: /account|profile/i }));
    expect(screen.getByText('me@x.io')).toBeInTheDocument();
  });
  it('Settings opens the settings view', () => {
    const setViewMode = vi.fn();
    render(<ProfileMenu {...base({ setViewMode })} />);
    fireEvent.click(screen.getByRole('button', { name: /account|profile/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /settings/i }));
    expect(setViewMode).toHaveBeenCalledWith('settings');
  });
  it('Log out calls onLogout', () => {
    const onLogout = vi.fn();
    render(<ProfileMenu {...base({ onLogout })} />);
    fireEvent.click(screen.getByRole('button', { name: /account|profile/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /log out/i }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });
  it('dark-mode item toggles the theme', () => {
    const setDarkMode = vi.fn();
    render(<ProfileMenu {...base({ setDarkMode })} />);
    fireEvent.click(screen.getByRole('button', { name: /account|profile/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /dark mode|light mode/i }));
    expect(setDarkMode).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/ProfileMenu.test.jsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement** — create `src/components/ProfileMenu.jsx` modeled on `PdfToolbarMenu.jsx`: a trigger button (`title="Account"`, `aria-haspopup="menu"`, a `User`/`UserCircle` icon from lucide-react) that toggles `open`; an outside-click + Escape `useEffect`; and a `role="menu"` panel with an identity header (`user.display_name || user.email`, and `user.email`), then `role="menuitem"` buttons: **Settings** → `() => { setViewMode('settings'); setOpen(false); }`; a dark-mode toggle → `() => setDarkMode(v => !v)` labeled `darkMode ? 'Light mode' : 'Dark mode'` (`Sun`/`Moon` icon); **Log out** → `() => onLogout()`.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/components/ProfileMenu.test.jsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/components/ProfileMenu.jsx src/components/ProfileMenu.test.jsx
git commit -m "feat(ui): ProfileMenu — Header dropdown for identity, settings, dark mode, logout

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `SettingsPage` scaffold + Voice & Reading, Connection, Appearance sections

**Files:**
- Create: `src/components/settings/SettingsPage.jsx`
- Test: `src/components/settings/SettingsPage.test.jsx`

**Interfaces:**
- Consumes: `voiceSettings`, `connectionSettings`, `appearanceSettings` (shapes above).
- Produces: `export default function SettingsPage({ theme, voiceSettings, chatSettings, connectionSettings, appearanceSettings, accountProps })` — Chat/Account sections are added in Tasks 5–6; scaffold renders all five headings.

- [ ] **Step 1: Write the failing test** — `src/components/settings/SettingsPage.test.jsx`:

```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SettingsPage from './SettingsPage';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const bags = (over = {}) => ({
  theme,
  voiceSettings: {
    selectedVoice: 'af_bella', setSelectedVoice: vi.fn(), playbackSpeed: 1.0, setPlaybackSpeed: vi.fn(),
    volume: 1, setVolume: vi.fn(), isLocalhost: true, setIsLocalhost: vi.fn(),
    requestTimeout: 30, setRequestTimeout: vi.fn(), unlimitedBatchTimeout: false, setUnlimitedBatchTimeout: vi.fn(),
    isPreviewingVoice: false, previewVoice: vi.fn(), stopVoicePreview: vi.fn(), clearCache: vi.fn(),
  },
  chatSettings: { inferenceSource: 'server', setInferenceSource: vi.fn(), chatTtsMode: 'streaming', setChatTtsMode: vi.fn(), chatAutoTts: true, setChatAutoTts: vi.fn(), inferenceByModel: {}, setInferenceByModel: vi.fn(), availableModels: [], selectedModel: '' },
  connectionSettings: { apiHost: '', setApiHost: vi.fn(), apiPort: '8000', setApiPort: vi.fn(), ollamaHost: '', setOllamaHost: vi.fn(), ollamaPort: '11434', setOllamaPort: vi.fn(), inferenceSource: 'server' },
  appearanceSettings: { darkMode: false, setDarkMode: vi.fn(), layoutMode: 'auto', setLayoutMode: vi.fn(), mobileBreakpoint: 768, setMobileBreakpoint: vi.fn(), showHeaderControlsOnMobile: false, setShowHeaderControlsOnMobile: vi.fn() },
  accountProps: { apiHost: '', apiPort: '8000', user: { email: 'me@x.io' } },
  ...over,
});

describe('SettingsPage — reader/global sections', () => {
  it('renders the five section headings', () => {
    render(<SettingsPage {...bags()} />);
    ['Voice & Reading', 'Chat & Inference', 'Connection', 'Appearance', 'Account']
      .forEach((h) => expect(screen.getByText(h)).toBeInTheDocument());
  });
  it('changing the speed calls setPlaybackSpeed', () => {
    const setPlaybackSpeed = vi.fn();
    const b = bags(); b.voiceSettings.setPlaybackSpeed = setPlaybackSpeed;
    render(<SettingsPage {...b} />);
    fireEvent.change(screen.getByLabelText(/speed/i), { target: { value: '1.5' } });
    expect(setPlaybackSpeed).toHaveBeenCalledWith(1.5);
  });
  it('toggling dark mode calls setDarkMode', () => {
    const setDarkMode = vi.fn();
    const b = bags(); b.appearanceSettings.setDarkMode = setDarkMode;
    render(<SettingsPage {...b} />);
    fireEvent.click(screen.getByLabelText(/dark mode/i));
    expect(setDarkMode).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/settings/SettingsPage.test.jsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement scaffold + three sections** — create `SettingsPage.jsx`: a scrollable full-height container (mirror `LibraryPage.jsx`'s outer wrapper) with an `<h1>Settings</h1>` and five `<section>`s each led by an `<h2>`/heading: **Voice & Reading**, **Chat & Inference** (empty placeholder comment for Task 5), **Connection**, **Appearance**, **Account** (placeholder for Task 6). Populate three now:
  - *Voice & Reading:* relocate the JSX from `Sidebar.jsx:81-225` (voice select + preview, speed, volume, request timeout, unlimited-batch) rebinding to `voiceSettings.*`; add the voice-backend Kokoro/System toggle bound to `voiceSettings.isLocalhost`/`setIsLocalhost`. Give the speed `<select>` an `aria-label="Speed"`.
  - *Connection:* relocate the API host/port block from `Sidebar.jsx:142-196` (bind `connectionSettings.apiHost/apiPort`); add the Ollama host/port block from `ChatSidebar.jsx:84-107` (bind `connectionSettings.ollamaHost/ollamaPort`), shown only when `connectionSettings.inferenceSource === 'local'`.
  - *Appearance & Layout:* a dark-mode control with `aria-label="Dark mode"` bound to `appearanceSettings.setDarkMode`; relocate the layout-mode block from `Sidebar.jsx:227-268` (bind `appearanceSettings.*`).

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/components/settings/SettingsPage.test.jsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/components/settings/SettingsPage.jsx src/components/settings/SettingsPage.test.jsx
git commit -m "feat(ui): SettingsPage scaffold + Voice/Connection/Appearance sections

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `SettingsPage` — Chat & Inference section (model selector + per-model knobs)

**Files:**
- Modify: `src/components/settings/SettingsPage.jsx`
- Modify: `src/components/settings/SettingsPage.test.jsx`

**Interfaces:**
- Consumes: `chatSettings` + `resolveForModel`/`patchForModel` from `src/hooks/inference.js`, `InferenceRow` from `src/components/chat/InferenceRow.jsx`, `InferenceSourceSelect` from `src/components/chat/InferenceSourceSelect.jsx`.

- [ ] **Step 1: Write the failing test** — add to `SettingsPage.test.jsx`:

```javascript
it('edits the chosen model\'s context window via the model selector', () => {
  const setInferenceByModel = vi.fn();
  const b = bags({});
  b.chatSettings = { ...b.chatSettings, availableModels: ['m1', 'm2'], selectedModel: 'm1',
    inferenceByModel: { m1: {}, m2: {} }, setInferenceByModel };
  render(<SettingsPage {...b} />);
  // defaults the config picker to selectedModel = m1
  expect(screen.getByLabelText(/configuring model/i)).toHaveValue('m1');
  fireEvent.change(screen.getByLabelText(/context window/i), { target: { value: '8192' } });
  expect(setInferenceByModel).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/settings/SettingsPage.test.jsx`
Expected: FAIL — no "Configuring model" control yet.

- [ ] **Step 3: Implement** — in the Chat & Inference section add: `InferenceSourceSelect` (bind `chatSettings.inferenceSource`/`setInferenceSource`); the read-aloud mode toggle + auto-read (relocate `ChatSidebar.jsx:160-193`, bind `chatSettings.chatTtsMode`/`chatAutoTts`); then a local `const [configModel, setConfigModel] = useState(chatSettings.selectedModel || chatSettings.availableModels[0] || '')` with a `<select aria-label="Configuring model">` over `chatSettings.availableModels`; and the four `InferenceRow`s bound to `resolveForModel(chatSettings.inferenceByModel, configModel)` values, each `onChange` calling `chatSettings.setInferenceByModel(prev => patchForModel(prev, configModel, { <key>: ... }))`. Copy the four rows' labels/options from `ChatSidebar.jsx:203-249`. Give the context-window row `label="Context window"` (its `<select>` is found by that accessible name).

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/components/settings/SettingsPage.test.jsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/settings/SettingsPage.jsx src/components/settings/SettingsPage.test.jsx
git commit -m "feat(ui): SettingsPage Chat & Inference section with per-model selector

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `SettingsPage` — Account section

**Files:**
- Modify: `src/components/settings/SettingsPage.jsx`
- Modify: `src/components/settings/SettingsPage.test.jsx`

**Interfaces:**
- Consumes: `accountProps = { apiHost, apiPort, user }`; renders `AccountPanel` from `src/components/account/AccountPanel.jsx` **without** `onLogout`.

- [ ] **Step 1: Write the failing test** — add to `SettingsPage.test.jsx` (mock apiFetch so AccountPanel mounts cleanly):

```javascript
vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })) }));

it('renders the Account panel (PAT management) in the Account section', async () => {
  render(<SettingsPage {...bags()} />);
  // AccountPanel shows a "Personal access tokens" heading / create control
  expect(await screen.findByText(/access token/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/settings/SettingsPage.test.jsx`
Expected: FAIL — Account section is an empty placeholder.

- [ ] **Step 3: Implement** — import `AccountPanel` and render it inside the Account section: `<AccountPanel theme={theme} apiHost={accountProps.apiHost} apiPort={accountProps.apiPort} user={accountProps.user} />` (no `onLogout`). Adjust the test's `/access token/i` matcher to whatever heading AccountPanel actually renders (read `AccountPanel.jsx`).

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/components/settings/SettingsPage.test.jsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/settings/SettingsPage.jsx src/components/settings/SettingsPage.test.jsx
git commit -m "feat(ui): SettingsPage Account section (relocated AccountPanel, no logout)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Wire `SettingsPage` + `ProfileMenu` into App and Header

**Files:**
- Modify: `src/App.jsx` (render the settings view; grouped bags; `inSettings` guards; Header props)
- Modify: `src/components/Header.jsx` (gear icon + `ProfileMenu`; remove the moved dark-mode/voice-backend quick controls)
- Test: `src/components/Header.jsx` — add/extend `src/components/Header.settings.test.jsx`

**Interfaces:**
- Consumes: `SettingsPage`, `ProfileMenu`, all App settings state.

- [ ] **Step 1: Write the failing test** — `src/components/Header.settings.test.jsx` (render Header with the props it needs; assert the gear opens settings and the profile menu is present). Because Header takes many props, build a `baseProps` factory mirroring `Header`'s destructure with `vi.fn()`s, then:

```javascript
it('the gear button switches to the settings view', () => {
  const setViewMode = vi.fn();
  render(<Header {...baseProps({ setViewMode })} />);
  fireEvent.click(screen.getByRole('button', { name: /settings/i }));
  expect(setViewMode).toHaveBeenCalledWith('settings');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/Header.settings.test.jsx`
Expected: FAIL — no settings gear yet.

- [ ] **Step 3: Implement** —
  - `App.jsx`: add `const inSettings = viewMode === 'settings';`. In the main-content ternary add a `inSettings ? <SettingsPage theme={theme} voiceSettings={{...}} chatSettings={{...}} connectionSettings={{...}} appearanceSettings={{...}} accountProps={{ apiHost, apiPort, user: auth.user }} /> :` branch (assemble the bags from existing state per the Shared-interfaces shapes). Extend every reader-only guard site that reads `!inChat && !inAdmin && !inLibrary` (e.g. App.jsx lines ~259, 392–400, and the sidebar/main gates) with `&& !inSettings`, and hide both sidebars when `inSettings` (mirror `inLibrary`/`inAdmin`). Pass Header new props: `user={auth.user}` and `onLogout={auth.logout}` (already passed), plus nothing else needed since Header builds ProfileMenu from existing `darkMode/setDarkMode/viewMode/setViewMode`.
  - `Header.jsx`: add a **gear** button (`Settings` icon) → `onClick={() => setViewMode('settings')}` with `title="Settings"`; add `<ProfileMenu theme={theme} user={user} darkMode={darkMode} setDarkMode={setDarkMode} setViewMode={setViewMode} onLogout={onLogout} />`; **remove** the standalone dark-mode toggle (now in ProfileMenu) and the Kokoro/System badge (now in Settings › Voice). Add `user` + `onLogout` to Header's prop destructure.

- [ ] **Step 4: Run tests**

Run: `npm run test:run && npm run lint`
Expected: Header settings test PASS; full suite green; no new lint errors.

- [ ] **Step 5: Commit**

```bash
git add src/App.jsx src/components/Header.jsx src/components/Header.settings.test.jsx
git commit -m "feat(ui): wire Settings view + ProfileMenu into App and Header

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> After this task the app has the new Settings page live **and** the old sidebar
> settings still present (harmless duplication). Tasks 8–9 remove the old copies.

---

### Task 8: Remove Settings / Account / Admin sections from the reader `Sidebar`

**Files:**
- Modify: `src/components/Sidebar.jsx`
- Test: `src/components/Sidebar.sections.test.jsx` (new)

**Interfaces:**
- The reader `Sidebar` keeps only Reading Stats + the Page Contents / Chapters (TOC) tabs.

- [ ] **Step 1: Write the failing test** — `src/components/Sidebar.sections.test.jsx`: render `Sidebar` with minimal props and assert the removed sections are gone while nav remains:

```javascript
it('no longer renders Settings / Account / Admin sections', () => {
  render(<Sidebar {...baseSidebarProps()} />);
  expect(screen.queryByRole('heading', { name: /^settings$/i })).toBeNull();
  expect(screen.queryByRole('heading', { name: /^account$/i })).toBeNull();
  expect(screen.queryByRole('heading', { name: /^admin$/i })).toBeNull();
});
```

Build `baseSidebarProps()` from `Sidebar`'s destructure with `vi.fn()`s and `pdfOutline: []`.

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/Sidebar.sections.test.jsx`
Expected: FAIL — those headings still render.

- [ ] **Step 3: Implement** — in `Sidebar.jsx` delete the Settings section (`67–271`), the Account section (`273–288`), and the Admin section (`290–308`). Remove now-unused imports (`Settings`, `User`, `Shield` icons, `AccountPanel`, `AdminPanel`) and the props only those sections used (voice/speed/volume/timeout/layout setters, `settingsOpen`, `accountOpen`, `user`/`onLogout` if unused elsewhere — verify by grep before deleting each). Keep Reading Stats + the sidebar-tabs/TOC blocks.

- [ ] **Step 4: Run tests**

Run: `npm run test:run && npm run lint`
Expected: new test PASS; full suite green; no unused-var lint errors.

- [ ] **Step 5: Commit**

```bash
git add src/components/Sidebar.jsx src/components/Sidebar.sections.test.jsx
git commit -m "refactor(ui): reader sidebar keeps navigation only; settings/account/admin moved out

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Remove the Settings section from `ChatSidebar`

**Files:**
- Modify: `src/components/ChatSidebar.jsx`
- Test: `src/components/ChatSidebar.settings-removed.test.jsx` (new)

**Interfaces:**
- `ChatSidebar` keeps the model picker + session list; the settings controls move to the Settings page.

- [ ] **Step 1: Write the failing test** — assert the settings controls are gone but the model picker + a session affordance remain:

```javascript
it('no longer renders the inference/read-aloud settings block', () => {
  render(<ChatSidebar {...baseChatSidebarProps()} />);
  expect(screen.queryByText(/read-aloud mode/i)).toBeNull();
  expect(screen.queryByText(/context window/i)).toBeNull();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/ChatSidebar.settings-removed.test.jsx`
Expected: FAIL — those controls still render.

- [ ] **Step 3: Implement** — remove the `<Section title="Settings">…</Section>` block (`ChatSidebar.jsx:63–255`: inference source, host/port, read-aloud mode, per-model knobs). Keep the model picker (move it out of the Settings section so it stays visible) and the Sessions section. Remove now-unused props (`inferenceSource`, `chatTtsMode`, `inference`, `ollamaHost`, …) — verify each with grep before deleting. Update `src/components/ChatSidebar.inference.test.jsx` / `ChatSidebar.meter.test.jsx` if they asserted on the removed block.

- [ ] **Step 4: Run tests**

Run: `npm run test:run && npm run lint`
Expected: new test PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/components/ChatSidebar.jsx src/components/ChatSidebar.settings-removed.test.jsx
git commit -m "refactor(ui): chat sidebar keeps model picker + sessions; settings moved out

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Remove the logout footer from `AccountPanel`

**Files:**
- Modify: `src/components/account/AccountPanel.jsx` (remove the Log out button at line 89 and the `onLogout` prop)
- Modify: `src/components/account/AccountPanel.test.jsx`

**Interfaces:**
- `AccountPanel({ theme, apiHost, apiPort, user })` — no `onLogout`; logout now lives only in `ProfileMenu`.

- [ ] **Step 1: Write the failing test** — update/add in `AccountPanel.test.jsx`:

```javascript
it('does not render a Log out button (logout lives in the profile menu)', () => {
  render(<AccountPanel theme={theme} apiHost="" apiPort="8000" user={{ email: 'me@x.io' }} />);
  expect(screen.queryByRole('button', { name: /log out/i })).toBeNull();
});
```

Remove any existing AccountPanel test that asserted logout fires.

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/account/AccountPanel.test.jsx`
Expected: FAIL — the Log out button still renders.

- [ ] **Step 3: Implement** — delete the Log out `<button>` (`AccountPanel.jsx:89`) and drop `onLogout` from the props destructure.

- [ ] **Step 4: Run tests**

Run: `npm run test:run && npm run lint`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/components/account/AccountPanel.jsx src/components/account/AccountPanel.test.jsx
git commit -m "refactor(ui): AccountPanel drops its logout button (now in ProfileMenu)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after Task 10)

- [ ] `npm run test:run` — full suite green.
- [ ] `npm run lint` — no new errors (only the pre-existing `useAuth.js:51`).
- [ ] `npm run build` — production build succeeds.
- [ ] **Manual smoke** (needs rebuild + redeploy to see live): open Settings from the gear and from the profile menu; change voice/speed/dark mode and confirm the effect; edit a per-model knob via the model selector; confirm reader sidebar shows only TOC/stats and chat sidebar shows only model picker + sessions; log out from the profile menu in each view.

## Spec coverage self-check

- Settings view + `CAP_FREE_VIEWS` → Task 1, 7. Five sections → Tasks 4–6. Model selector → Task 5. Profile menu (identity/settings/dark/logout) → Task 3, 7. Dark mode both places → Task 3 (menu) + Task 4 (Appearance). Sidebars keep nav; Admin removed → Task 8. Chat settings out → Task 9. AccountPanel relocation + logout removal → Task 6, 10. No backend/migration → holds (all tasks frontend-only).
