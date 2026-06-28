# Markdown Folder Workspace — Design

- **Date:** 2026-06-28
- **Status:** Approved (design), pending implementation plan
- **Author:** brainstorming session
- **Component area:** uploaded-Markdown reader (`MarkdownPageRenderer` path)

## Problem

When a user opens a single `.md` file, links to other files do not navigate. The
app loads one file at a time into the IndexedDB `books` store (keyed by
`fileName`, capped at 5, raw bytes), so it has no sibling files to resolve a
relative link against. Both Markdown renderers also render every `<a>` with
`target="_blank"` against the web origin, so a relative link such as
`[other](./other.md)` simply 404s. In-page `#heading` anchors and relative
`<img>` assets are likewise non-functional for the same reason (no folder
context, no heading IDs).

## Goals

Open a **folder as a workspace** so a set of linked Markdown files behaves like a
small local docs site / wiki:

1. **Cross-file navigation** — relative links between `.md`/`.txt` files in the
   folder resolve and load the target as the active document.
2. **In-page anchors** — `#heading` links scroll within the current document;
   `other.md#section` loads the file, then scrolls.
3. **Relative images/assets** — `<img src="./img/x.png">` renders from the
   folder.
4. **Browser-like history** — Back/Forward to retrace the link path.
5. **Offline, all browsers** — no backend dependency; works in Chromium and in
   Firefox/Safari (with a graceful capability difference for persistence).

## Non-Goals (v1)

- Sidebar file-tree browser of the whole folder (candidate for phase 2).
- Opening relative **PDF** links in the PDF reader (deferred; `.md`/`.txt` only).
- Editing/writing files back to the folder.
- A backend-served folder option (rejected; keeps the feature browser-only).
- Wiring cross-file navigation into the docling-converted `MarkdownReader.jsx`
  (its links originate from PDF conversion and rarely cross-file).

## Locked Decisions

| Decision | Choice |
|----------|--------|
| Folder access | **Hybrid**: File System Access API where supported, `webkitdirectory` snapshot fallback |
| Click on internal link | **Replace** the active document + maintain **Back/Forward** history |
| Scope | Folder workspace: cross-file nav + anchors + relative images |
| Persistence | FSA handle persisted & re-permissioned; snapshot re-picked on reload |

## Architecture

### 1. `Workspace` abstraction — `src/lib/workspace.js`

One uniform interface, two interchangeable backends, so the renderer/reader code
is backend-agnostic:

```
Workspace = {
  kind: 'fsa' | 'snapshot',
  rootName: string,
  hasFile(relPath): boolean,
  listFiles(): string[],                 // normalized relative paths
  readText(relPath): Promise<string>,    // .md / .txt
  readBlob(relPath): Promise<Blob>,      // images / future assets
  handle?: FileSystemDirectoryHandle,    // FSA backend only
}
```

- **FSA backend** (`createFsaWorkspace(handle)`): recursively walk the directory
  handle **once** into `Map<relPath, FileSystemFileHandle>`. `readText`/`readBlob`
  call `getFile()` on demand → memory-light, suitable for large vaults.
- **Snapshot backend** (`createSnapshotWorkspace(fileList)`): build
  `Map<relPath, File>` from `input.files` (each `File.webkitRelativePath` with the
  leading root segment stripped). Reads come straight from the in-memory `File`.

Both index keys are normalized: forward slashes, no leading `./`, case preserved.

**Entry file** (which document opens when a folder is first picked): the first
match, case-insensitive, of `README.md`, `readme.markdown`, `index.md` at the
folder root; otherwise the first `.md`/`.markdown` by case-insensitive
alphabetical relative path. If the folder contains no Markdown, show a toast and
stay in the prior state.

### 2. `WorkspaceContext` — `src/lib/WorkspaceContext.jsx`

React context exposing `{ workspace, currentPath, navigate(relPath, anchor?),
resolveAsset(relPath), goBack(), goForward(), canGoBack, canGoForward }` so
markdown components resolve links/images without prop-drilling. Holds the
history stack and the currently-active workspace-relative path.

### 3. Path resolution — `src/utils/resolvePath.js` (pure, unit-tested)

`resolvePath(currentDir, href) -> { path, anchor } | { external: true } | { anchorOnly }`

- Classifies `href`: external (`http(s):`, `mailto:`, protocol-relative),
  anchor-only (`#x`), or relative path.
- For relative paths: split off `#anchor` and `?query`, `decodeURIComponent`,
  posix-join with `currentDir`, normalize `.`/`..`, strip leading `./`.
- Returns the normalized workspace-relative path + optional anchor.

This is the highest-value unit to test in isolation (many edge cases, no I/O).

### 4. Link interception (renderer change)

Replace the `a` component in [MarkdownPageRenderer.jsx](../../../src/components/MarkdownPageRenderer.jsx)
with a `WorkspaceLink` that consumes `WorkspaceContext`:

- **External** → existing behavior (`target="_blank" rel="noopener noreferrer"`).
- **Anchor-only** (`#h`) → `preventDefault`; scroll to the element whose id is the
  slug; update `location.hash` without reload.
- **Relative path**:
  - Resolve via `resolvePath(dirname(currentPath), href)`.
  - If `hasFile(path)` and it is `.md`/`.markdown`/`.txt` → `preventDefault` and
    `navigate(path, anchor)`.
  - If not found → `preventDefault` + toast "linked file isn't in this folder".
- No workspace active (single-file mode) → fall back to current `target="_blank"`
  behavior unchanged.

Heading IDs come from adding **`rehype-slug`** to the paginated renderer
([MarkdownPageRenderer.jsx](../../../src/components/MarkdownPageRenderer.jsx))
only; the converted `MarkdownReader.jsx` is untouched in v1 (per Non-Goals).
Slugger output must match the slug we compute when scrolling to
`other.md#section`, so anchor scrolling reuses the same `github-slugger` logic.

### 5. Relative image/asset resolution

Replace the `img` component: if `src` is relative and `workspace.hasFile`,
`readBlob` → `URL.createObjectURL` → render; collect URLs and **revoke on
unmount / document swap** to avoid leaks. External/`data:` srcs render as-is.
Missing assets fall back to native broken-image (acceptable v1).

### 6. Reader integration — active-doc swap + history

- `navigate(relPath, anchor)`:
  1. `readBlob`/`readText` the target; synthesize `new File([bytes], basename,
     { type })`.
  2. Feed it through the **existing** `processFile` pipeline in
     [App.jsx](../../../src/App.jsx) so pagination, sentence highlight, TTS, and
     audiobook all work on the linked file with no renderer changes.
  3. Push `relPath` onto the history stack (truncating any forward entries);
     set `currentPath`.
  4. After render, if `anchor`, scroll to the matching heading id.
- **Back/Forward** buttons appear in the PDF/MD toolbar only when a workspace is
  active; they move the history pointer and re-`navigate` without re-pushing.
- Synthesized files are **not** persisted to the 5-doc `books` library (they live
  in the workspace); only the workspace + last path are persisted (see §7). This
  avoids evicting the user's real library via the LRU cap.

### 7. Persistence (IndexedDB `db.js`, v3 → v4)

Add a small `workspaces` object store (or a generic `kv` store):
`{ id: 'last', rootName, handle?, lastPath }`.

- **FSA**: `FileSystemDirectoryHandle` is structured-clonable → persist it. On
  app load, read it, `queryPermission({mode:'read'})`; if `granted`, rebuild the
  workspace silently and restore `lastPath`. If `prompt`, show a one-click
  **"Reconnect folder"** (browsers require a user gesture to `requestPermission`).
- **Snapshot**: `File`s from an input are not durably re-readable after reload →
  cannot auto-restore. Persist only `rootName` + `lastPath`; on load show a
  **"Re-open folder"** prompt, and after re-pick restore `lastPath` so the user
  lands back where they were.

### 8. Entry points

- **WelcomeScreen**: an **"Open folder"** button beside "Open file". Uses
  `showDirectoryPicker()` when available, else a hidden `<input type="file"
  webkitdirectory>`.
- **Header**: show the active workspace `rootName` (and a way to close it /
  return to single-file mode).

## Data Flow (happy path, FSA)

```
User picks folder
  → showDirectoryPicker() → handle
  → createFsaWorkspace(handle) walks tree → index Map
  → persist handle + open the folder's entry .md (README.md or first .md) via navigate()
  → processFile(synth File) → reader renders with WorkspaceContext
User clicks [b](./sub/b.md)
  → WorkspaceLink → resolvePath('.', './sub/b.md') → 'sub/b.md'
  → workspace.hasFile → navigate('sub/b.md')
  → readText → synth File → processFile → push history → render
```

## Error Handling / Edge Cases

- Target not in folder → toast, no navigation, no crash.
- Permission revoked / lost (FSA) mid-session → catch on read, prompt reconnect.
- Symlink loops / very deep trees → cap recursion depth during indexing; skip
  unreadable entries.
- Non-text targets (e.g. `.png` clicked as a link) → treated as asset/no-op in
  v1 (only `.md`/`.txt` navigate).
- Anchor slug mismatch → scroll no-ops gracefully (stay at top of new doc).
- Large folders → FSA reads lazily; snapshot warns if total size is very large.
- Filename collisions across subfolders → keys are full relative paths, so no
  collision.

## Browser Support Matrix

| Capability | Chromium (Chrome/Edge/Brave) | Firefox / Safari |
|-----------|------------------------------|------------------|
| Open folder | FSA `showDirectoryPicker` | `webkitdirectory` input |
| Lazy on-demand reads | Yes | No (in-memory snapshot) |
| Persist & auto-restore across reload | Yes (re-permission) | No (re-pick) |
| Cross-file nav / anchors / images | Yes | Yes |

## New Dependencies

- **`rehype-slug`** (+ its `github-slugger`) — heading IDs for anchor links.
  Small, well-maintained, no runtime/network cost. Everything else uses native
  browser APIs (File System Access, `webkitdirectory`, Blob URLs).

## Testing Strategy

- **Unit (pure):** `resolvePath` — `./`, `../`, nested, `%20` spaces, `#anchor`
  split, `?query` strip, external classification, anchor-only.
- **Unit (with fakes):** workspace index builder against a fake directory handle
  and a fake `webkitRelativePath` file list — `hasFile`, `listFiles`,
  normalization, root-segment stripping.
- **Component:** `WorkspaceLink` routing (external vs anchor vs relative vs
  missing) with a mocked context; image resolver creates/revokes object URLs.
- **Manual:** full FSA flow in Chrome (open, navigate, back/forward, reload
  restore) and snapshot flow in Firefox (open, navigate, reload re-pick).

## Affected / New Files

**New**
- `src/lib/workspace.js` — abstraction + `createFsaWorkspace` / `createSnapshotWorkspace`
- `src/lib/WorkspaceContext.jsx` — context, history stack, navigate/back/forward
- `src/utils/resolvePath.js` — pure path resolver
- `src/components/WorkspaceLink.jsx` — smart `<a>` (or inline in renderer components)
- Tests for the above

**Modified**
- `src/components/MarkdownPageRenderer.jsx` — use `WorkspaceLink` + workspace-aware
  `img`; add `rehype-slug`
- `src/App.jsx` — folder open handler, workspace state, `navigate` wiring into
  `processFile`, Back/Forward toolbar state
- `src/components/WelcomeScreen.jsx` — "Open folder" entry point
- `src/components/Header.jsx` — show workspace name / close-workspace control
- `src/db.js` — v3 → v4 migration adding `workspaces`/`kv` store
- `package.json` — add `rehype-slug`

## Future (Phase 2+)

- Sidebar file-tree of the folder.
- Open relative PDFs in the PDF reader; other asset types.
- Optional cross-file linking in the docling-converted reader.
- "Index whole folder" for RAG across the workspace (ties into existing pgvector
  indexing).
