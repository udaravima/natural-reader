# App Shell — Settings page + profile menu (P3, part 1)

**Date:** 2026-09-22 · **Status:** design, awaiting review · **Branch (planned):** `development` (or a `feat/app-shell-settings` topic branch) · **Type:** frontend-only, no backend/migration.

## Summary

Consolidate every scattered setting into a single full-page **Settings view**
(`viewMode: 'settings'`), and give the app a modern **profile-icon menu** in the
persistent Header that houses identity, a dark-mode quick toggle, and Log out.
Today's settings are split across two sidebars (reader `Sidebar`, `ChatSidebar`)
and the Header; logout is buried in an account panel inside the reader sidebar,
reachable only in reader view.

This is **part 1 of P3** (the app shell). **Background TTS** — keeping read-aloud
playing across tab switches — is explicitly deferred to a **part-2 spec**, because
it needs a persistent playback control and a global "one TTS aloud at a time"
rule that ride on this shell.

## Motivation

- The SPA has no router; `viewMode` swaps whole screens and unmounts the rest.
  Settings live inside the per-view sidebars, so they're context-bound and
  duplicated in feel across reader vs chat.
- **Logout is buried** in `AccountPanel` inside the reader `Sidebar`
  ([Sidebar.jsx:286](../../../src/components/Sidebar.jsx#L286)) — only reachable
  in reader view. A user in chat/library/admin has no visible way to sign out.
- The reader and chat sidebars are cluttered with configuration that the user
  touches rarely, crowding out the navigation content that belongs there.

## Decisions (locked in brainstorming, 2026-09-22)

1. **Scope:** Settings page + profile menu now; **background TTS is a separate
   later spec**.
2. **Everything moves to the Settings page** — all reader and chat settings
   consolidate; the sidebars keep only navigation/operational content.
3. **Form:** a **dedicated view** (`viewMode: 'settings'`), consistent with the
   existing reader/chat/library/admin model — not a modal or slide-over.
4. **Per-model inference knobs:** the Settings page gets a **model selector** at
   the top of the Chat & Inference section, so the four per-model knobs
   (context window, keep-warm, thinking, max reply tokens) apply to a chosen
   model.
5. **Dark mode:** a **quick toggle in the profile menu** *and* under Settings ›
   Appearance.

## Architecture

- Add `viewMode: 'settings'` — rendered as a full-page view in App's main-content
  ternary, alongside `library`/`admin` (they hide the sidebars; so does this).
- **We move the controls, not the state.** Every setting below is already an
  App-level `usePersistedState` (or equivalent) value. `SettingsPage` and
  `ProfileMenu` receive the same values + setters as props. **No new persistence,
  no backend change, no migration, no behavior change** to what the settings do —
  purely a relocation of the UI that reads/writes them.
- `settings` joins **`CAP_FREE_VIEWS`** in
  [useViewModeGuard.js](../../../src/hooks/useViewModeGuard.js) (like Library —
  available to any active user, not capability-gated; not added to `VIEW_ORDER`
  since it's never a coercion fallback target).
- Entry points in the persistent Header: a **gear icon** (opens Settings
  directly) and a **Settings** item inside the profile menu.
- `settings` is **not** a ViewSwitcher tab — it's reached via the gear/profile
  menu, so `ViewSwitcher` is unchanged.

## Settings page — sections and exact contents

A full-height scrollable page (`src/components/settings/SettingsPage.jsx`) with
five labeled sections. Contents are the exact controls being relocated:

1. **Voice & Reading** (from reader `Sidebar`): voice select + preview, speed,
   volume, voice backend (Kokoro/System — `isLocalhost`, from the Header), TTS
   request timeout, unlimited batch/download timeout.
2. **Chat & Inference** (from `ChatSidebar`): inference source (Server/Local),
   read-aloud mode (streaming / after-complete) + auto-read toggle, and — under a
   **"Configuring model: [picker]"** selector — the four per-model knobs
   (`numCtx`, `keepAlive`, `think`, `numPredict` via `InferenceRow`).
3. **Connection**: reader API host/port; Ollama host/port (shown only in Local
   inference mode).
4. **Appearance & Layout**: dark mode; layout mode (auto/desktop/mobile); mobile
   breakpoint width; "show header controls on mobile".
5. **Account**: the existing `AccountPanel` (personal-access-token create / list /
   revoke), relocated here. Its **logout footer is removed** — logout now lives in
   the profile menu.

## Profile menu (Header)

`src/components/ProfileMenu.jsx` — a profile/avatar icon in the Header opening a
small dropdown (same open/outside-click/Escape pattern as `HeaderOverflowMenu` /
`PdfToolbarMenu`):

- Identity header: display name / email (`auth.user`).
- **Settings** → `setViewMode('settings')`.
- **Dark mode** quick toggle → `setDarkMode`.
- **Log out** → `auth.logout` (the `onLogout` currently threaded to the sidebar).

Heavier PAT management is **not** in the dropdown — it lives on the Settings page
(§5).

## What stays, what's removed

**Stays in the sidebars (navigation/operational):**
- Reader `Sidebar`: **Reading Stats**, and the **Page Contents / Chapters (TOC)**
  tabs ([Sidebar.jsx:309-420](../../../src/components/Sidebar.jsx#L309)). The
  sidebar becomes a focused reading-navigation panel.
- `ChatSidebar`: the **model picker** (operational — switched mid-chat) and the
  **session list** (navigation).

**Removed / relocated:**
- Reader `Sidebar`: the entire **Settings** section (→ Settings page), the
  **Account** section (→ Settings page §5), and the legacy **Admin** section
  (`AdminPanel`, admins only, [Sidebar.jsx:290-308](../../../src/components/Sidebar.jsx#L290))
  — redundant with the Admin **view**, so deleted.
- `ChatSidebar`: the entire **Settings** section (inference source, host/port,
  read-aloud mode, per-model knobs) → Settings page §2/§3.
- Header: dark-mode and voice-backend quick controls move into the profile menu /
  Settings; the `inReader` playback controls stay as-is (background TTS is part 2).

## Component plan

| File | Change |
|---|---|
| `src/components/settings/SettingsPage.jsx` | **new** — the view; renders the 5 sections. Sections extracted into `settings/*Section.jsx` if the file grows past ~200 lines. |
| `src/components/ProfileMenu.jsx` | **new** — Header profile dropdown. |
| `src/App.jsx` | add `inSettings`, render `<SettingsPage>` for `viewMode==='settings'`; thread settings values/setters (grouped prop bags to avoid a 40-prop signature); extend the `!inChat && !inAdmin && !inLibrary` guard sites with `&& !inSettings`; render `<ProfileMenu>`/gear via Header props. |
| `src/hooks/useViewModeGuard.js` | add `'settings'` to `CAP_FREE_VIEWS`. |
| `src/components/Header.jsx` | add the gear icon + `ProfileMenu`; remove the dark-mode/voice-backend quick controls that moved. |
| `src/components/Sidebar.jsx` | remove Settings + Account + Admin sections; keep Reading Stats + TOC tabs. |
| `src/components/ChatSidebar.jsx` | remove the Settings section; keep model picker + sessions. |
| `src/components/AccountPanel.jsx` | drop the logout footer (logout is in ProfileMenu); otherwise rendered from SettingsPage. |

**Interface note:** to avoid threading ~20 individual settings through App →
SettingsPage, group them into cohesive prop bags (e.g. `voiceSettings`,
`inferenceSettings`, `connectionSettings`, `appearanceSettings`) or a small
`SettingsContext`. Grouped prop bags are the default; a context only if drilling
proves noisy.

## Testing

- **SettingsPage**: each section renders; each control reflects its incoming
  value and calls its setter on change (RTL, props are the same setters used
  today). The Chat & Inference **model selector** switches which model's knobs
  are shown/edited.
- **useViewModeGuard**: `'settings'` is left alone for any active user (extend
  the existing guard test, mirroring the Library cases).
- **ProfileMenu**: shows identity; Settings item calls `setViewMode('settings')`;
  dark toggle calls `setDarkMode`; Log out calls `onLogout`; outside-click/Escape
  close (reuse the pattern's coverage).
- **AccountPanel**: PAT create/list/revoke still work from the new location;
  existing AccountPanel tests pass after the import move; no logout button.
- **Regression**: sidebars no longer render Settings/Account/Admin; reader TOC +
  chat sessions/model-picker still render and function.

## Rollout & risks

- **Frontend-only**; no backend, no migration, no feature flag (a UI reorg).
  Needs `npm run build` + redeploy to `dist/` to appear live.
- **Main risk:** the large deletions from two sidebars could drop a control that
  App still passes props for, or orphan a prop. Mitigation: move (don't
  re-implement) each control; grep every relocated prop's usages; run the full
  frontend suite; the settings values are unchanged App state, so behavior is
  verifiable by toggling on the new page and confirming the old effect.
- Secondary: prop volume into SettingsPage — mitigated by grouped bags.

## Out of scope (named, not dropped)

- **Background TTS** across tab switches → **P3 part 2** (needs a persistent
  playback control in the Header + a one-TTS-at-a-time rule; the engine is
  already app-level, so no engine move required).
- Projects/permissions UI (GitLab-style), inference backends (OpenRouter/vLLM),
  RAG phases 1–3 — separate tracks.
