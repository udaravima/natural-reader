# Markdown Folder Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user open a folder of Markdown so relative links between `.md`/`.txt` files navigate in-app, `#heading` anchors scroll, and relative images render.

**Architecture:** A backend-agnostic `Workspace` (File System Access API where supported, `webkitdirectory` snapshot fallback) indexes a folder into `relPath → file`. A `WorkspaceContext` holds a Back/Forward history stack and drives an `onOpenDoc(path)` callback that reuses the existing `usePdfEngine` text/markdown loaders — so pagination, highlight, and TTS work on the linked file unchanged. Smart `<a>`/`<img>` components in the Markdown renderer resolve relative paths against the active file's directory.

**Tech Stack:** React 19, Vite (Rolldown), react-markdown + remark-gfm, `rehype-slug` (new), IndexedDB, File System Access API, Vitest + Testing Library (new, for tests).

## Global Constraints

- Node.js `^20.19.0 || >=22.12.0`; never introduce code requiring a newer floor without flagging.
- **100% offline / browser-only.** No new backend dependency; the FastAPI server must not be required for this feature.
- Reader-mode behavior for single-file (non-workspace) use must remain **byte-for-byte unchanged** — workspace code paths activate only when a workspace is open.
- Synthesized linked files are **NOT** written to the IndexedDB `books` library (avoid evicting the user's 5-doc LRU). Only workspace metadata + last path persist.
- Scope is the uploaded-Markdown renderer `MarkdownPageRenderer.jsx` only; the docling `MarkdownReader.jsx` is untouched.
- Follow existing patterns: function components, hooks, Tailwind classes, `lucide-react` icons, no TypeScript (`.js`/`.jsx`).

---

## File Structure

**New runtime files**
- `src/utils/resolvePath.js` — pure relative-path/anchor resolver.
- `src/lib/workspace.js` — `Workspace` abstraction + FSA/snapshot factories + `pickEntryFile`.
- `src/lib/WorkspaceContext.jsx` — provider, history stack, `navigate`/`goBack`/`goForward`, `useWorkspace`.
- `src/components/WorkspaceLink.jsx` — smart `<a>` and `WorkspaceImage` for the renderer.

**New test files**
- `src/utils/resolvePath.test.js`, `src/lib/workspace.test.js`, `src/lib/db.workspace.test.js`, `src/lib/WorkspaceContext.test.jsx`, `src/components/WorkspaceLink.test.jsx`.

**Modified**
- `src/hooks/usePdfEngine.js` — surface `loadMarkdownDocument`/`loadTextDocument` in the return.
- `src/db.js` — bump to v4, add `saveWorkspaceState`/`getWorkspaceState`/`clearWorkspaceState`.
- `src/components/MarkdownPageRenderer.jsx` — use `WorkspaceLink`/`WorkspaceImage`, add `rehype-slug`.
- `src/App.jsx` — folder-open handler, `WorkspaceProvider` wiring, `onOpenDoc`, restore-on-load, Back/Forward toolbar state.
- `src/components/WelcomeScreen.jsx` — "Open folder" button + hidden `webkitdirectory` input.
- `src/components/Header.jsx` — active-workspace badge + close control.
- `package.json` — add `rehype-slug`; dev: `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `fake-indexeddb`; `test` scripts.

---

### Task 0: Test infrastructure (Vitest + Testing Library)

**Files:**
- Modify: `package.json`
- Create: `vitest.config.js`, `src/test/setup.js`

**Interfaces:**
- Produces: `npm test` (watch) and `npm run test:run` (CI/one-shot) running Vitest with a jsdom environment and `@testing-library/jest-dom` matchers.

- [ ] **Step 1: Install dependencies**

Run:
```bash
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event fake-indexeddb
npm install rehype-slug github-slugger
```
Expected: packages added; `npm ls vitest` shows a version.

- [ ] **Step 2: Add test scripts to `package.json`**

In the `"scripts"` block add:
```json
"test": "vitest",
"test:run": "vitest run"
```

- [ ] **Step 3: Create `vitest.config.js`**

```js
import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        environment: 'jsdom',
        globals: true,
        setupFiles: ['./src/test/setup.js'],
        css: false,
    },
});
```

- [ ] **Step 4: Create `src/test/setup.js`**

```js
import '@testing-library/jest-dom/vitest';
```

- [ ] **Step 5: Add a smoke test to verify the runner**

Create `src/test/smoke.test.js`:
```js
import { describe, it, expect } from 'vitest';

describe('test runner', () => {
    it('runs', () => {
        expect(1 + 1).toBe(2);
    });
});
```

- [ ] **Step 6: Run the smoke test**

Run: `npm run test:run -- src/test/smoke.test.js`
Expected: PASS (1 passed).

- [ ] **Step 7: Commit**

```bash
git add package.json package-lock.json vitest.config.js src/test/setup.js src/test/smoke.test.js
git commit -m "test: add vitest + testing-library infrastructure"
```

---

### Task 1: `resolvePath` pure path resolver

**Files:**
- Create: `src/utils/resolvePath.js`
- Test: `src/utils/resolvePath.test.js`

**Interfaces:**
- Produces:
  - `isExternal(href: string): boolean`
  - `dirname(relPath: string): string` — `'a/b/c.md' → 'a/b'`, `'c.md' → ''`
  - `resolvePath(currentDir: string, href: string): Result` where `Result` is one of:
    - `{ kind: 'none' }`
    - `{ kind: 'anchor', anchor: string }`
    - `{ kind: 'external', href: string }`
    - `{ kind: 'path', path: string, anchor: string|null }` (path is normalized, no leading `./`)

- [ ] **Step 1: Write the failing test**

`src/utils/resolvePath.test.js`:
```js
import { describe, it, expect } from 'vitest';
import { resolvePath, isExternal, dirname } from './resolvePath';

describe('isExternal', () => {
    it('flags http/https/mailto/protocol-relative', () => {
        expect(isExternal('http://x')).toBe(true);
        expect(isExternal('https://x')).toBe(true);
        expect(isExternal('mailto:a@b.c')).toBe(true);
        expect(isExternal('//cdn/x')).toBe(true);
        expect(isExternal('./a.md')).toBe(false);
        expect(isExternal('a.md')).toBe(false);
    });
});

describe('dirname', () => {
    it('returns parent dir or empty', () => {
        expect(dirname('a/b/c.md')).toBe('a/b');
        expect(dirname('c.md')).toBe('');
    });
});

describe('resolvePath', () => {
    it('classifies empty and anchor-only', () => {
        expect(resolvePath('a', '')).toEqual({ kind: 'none' });
        expect(resolvePath('a', '#Heading%20One')).toEqual({ kind: 'anchor', anchor: 'Heading One' });
    });
    it('classifies external', () => {
        expect(resolvePath('a', 'https://x.com')).toEqual({ kind: 'external', href: 'https://x.com' });
    });
    it('resolves ./ and bare names against currentDir', () => {
        expect(resolvePath('docs', './b.md')).toEqual({ kind: 'path', path: 'docs/b.md', anchor: null });
        expect(resolvePath('docs', 'b.md')).toEqual({ kind: 'path', path: 'docs/b.md', anchor: null });
    });
    it('resolves ../ and nested', () => {
        expect(resolvePath('docs/sub', '../top.md')).toEqual({ kind: 'path', path: 'docs/top.md', anchor: null });
        expect(resolvePath('docs', 'sub/c.md')).toEqual({ kind: 'path', path: 'docs/sub/c.md', anchor: null });
    });
    it('splits #anchor and strips ?query and decodes %20', () => {
        expect(resolvePath('docs', './a%20b.md?x=1#sec')).toEqual({ kind: 'path', path: 'docs/a b.md', anchor: 'sec' });
    });
    it('treats leading slash as workspace-root absolute', () => {
        expect(resolvePath('docs/sub', '/top.md')).toEqual({ kind: 'path', path: 'top.md', anchor: null });
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/utils/resolvePath.test.js`
Expected: FAIL (cannot find module `./resolvePath`).

- [ ] **Step 3: Write the implementation**

`src/utils/resolvePath.js`:
```js
// Resolves Markdown link hrefs to workspace-relative paths (or classifies them
// as external / anchor). Pure: no I/O, fully unit-tested.

const EXTERNAL_RE = /^([a-z][a-z0-9+.-]*:|\/\/)/i; // http:, https:, mailto:, //cdn

export function isExternal(href) {
    return EXTERNAL_RE.test(href || '');
}

export function dirname(relPath) {
    const i = (relPath || '').lastIndexOf('/');
    return i === -1 ? '' : relPath.slice(0, i);
}

// Collapse '.' and '..' segments in a posix-style path.
function normalize(path) {
    const out = [];
    for (const seg of path.split('/')) {
        if (seg === '' || seg === '.') continue;
        if (seg === '..') { out.pop(); continue; }
        out.push(seg);
    }
    return out.join('/');
}

export function resolvePath(currentDir, href) {
    const raw = (href || '').trim();
    if (!raw) return { kind: 'none' };
    if (raw.startsWith('#')) return { kind: 'anchor', anchor: decodeURIComponent(raw.slice(1)) };
    if (isExternal(raw)) return { kind: 'external', href: raw };

    const hashIdx = raw.indexOf('#');
    const anchor = hashIdx === -1 ? null : decodeURIComponent(raw.slice(hashIdx + 1));
    let pathPart = hashIdx === -1 ? raw : raw.slice(0, hashIdx);
    const qIdx = pathPart.indexOf('?');
    if (qIdx !== -1) pathPart = pathPart.slice(0, qIdx);
    pathPart = decodeURIComponent(pathPart);

    const base = pathPart.startsWith('/') ? '' : (currentDir || '');
    const joined = base ? `${base}/${pathPart}` : pathPart;
    return { kind: 'path', path: normalize(joined), anchor };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- src/utils/resolvePath.test.js`
Expected: PASS (all cases green).

- [ ] **Step 5: Commit**

```bash
git add src/utils/resolvePath.js src/utils/resolvePath.test.js
git commit -m "feat: add resolvePath workspace link resolver"
```

---

### Task 2: `Workspace` abstraction (snapshot + FSA backends)

**Files:**
- Create: `src/lib/workspace.js`
- Test: `src/lib/workspace.test.js`

**Interfaces:**
- Produces:
  - `isMarkdownPath(p)`, `isTextPath(p)`, `isNavigable(p)` → booleans
  - `createSnapshotWorkspace(fileList): Workspace`
  - `indexDirectoryHandle(dirHandle, prefix?, index?, depth?): Promise<Map<string, FileSystemFileHandle>>`
  - `createFsaWorkspace(dirHandle): Promise<Workspace>`
  - `pickEntryFile(paths: string[]): string|null`
  - `Workspace = { kind, rootName, hasFile(p), listFiles(), readText(p), readBlob(p), handle? }`

- [ ] **Step 1: Write the failing test**

`src/lib/workspace.test.js`:
```js
import { describe, it, expect } from 'vitest';
import {
    createSnapshotWorkspace, indexDirectoryHandle, pickEntryFile, isNavigable,
} from './workspace';

function fakeFile(relPath, content) {
    const f = new File([content], relPath.split('/').pop(), { type: 'text/markdown' });
    Object.defineProperty(f, 'webkitRelativePath', { value: relPath });
    return f;
}

describe('createSnapshotWorkspace', () => {
    it('strips the root segment and indexes by relative path', async () => {
        const ws = createSnapshotWorkspace([
            fakeFile('vault/README.md', '# Home'),
            fakeFile('vault/sub/b.md', '# B'),
        ]);
        expect(ws.rootName).toBe('vault');
        expect(ws.hasFile('README.md')).toBe(true);
        expect(ws.hasFile('sub/b.md')).toBe(true);
        expect(ws.hasFile('vault/README.md')).toBe(false);
        expect(ws.listFiles().sort()).toEqual(['README.md', 'sub/b.md']);
        expect(await ws.readText('sub/b.md')).toBe('# B');
    });
});

describe('indexDirectoryHandle', () => {
    it('walks a fake handle recursively', async () => {
        const fileHandle = (name) => ({ kind: 'file', name, getFile: async () => new File(['x'], name) });
        const dir = {
            kind: 'directory', name: 'root',
            async *entries() {
                yield ['a.md', fileHandle('a.md')];
                yield ['sub', {
                    kind: 'directory', name: 'sub',
                    async *entries() { yield ['c.md', fileHandle('c.md')]; },
                }];
            },
        };
        const index = await indexDirectoryHandle(dir);
        expect([...index.keys()].sort()).toEqual(['a.md', 'sub/c.md']);
    });
});

describe('pickEntryFile', () => {
    it('prefers README.md, then index.md, then first md alphabetically', () => {
        expect(pickEntryFile(['z.md', 'README.md', 'a.md'])).toBe('README.md');
        expect(pickEntryFile(['z.md', 'index.md', 'a.md'])).toBe('index.md');
        expect(pickEntryFile(['z.md', 'a.md'])).toBe('a.md');
        expect(pickEntryFile(['notes.txt'])).toBe(null);
    });
});

describe('isNavigable', () => {
    it('accepts md/markdown/txt only', () => {
        expect(isNavigable('a.md')).toBe(true);
        expect(isNavigable('a.markdown')).toBe(true);
        expect(isNavigable('a.txt')).toBe(true);
        expect(isNavigable('a.png')).toBe(false);
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/lib/workspace.test.js`
Expected: FAIL (cannot find module `./workspace`).

- [ ] **Step 3: Write the implementation**

`src/lib/workspace.js`:
```js
// A folder "workspace" that resolves relative paths to file contents, with two
// interchangeable backends: File System Access API (lazy) and a webkitdirectory
// snapshot (in-memory). The rest of the app is backend-agnostic.

const MD_EXT = /\.(md|markdown)$/i;
const TEXT_EXT = /\.txt$/i;

export function isMarkdownPath(p) { return MD_EXT.test(p || ''); }
export function isTextPath(p) { return TEXT_EXT.test(p || ''); }
export function isNavigable(p) { return isMarkdownPath(p) || isTextPath(p); }

export function createSnapshotWorkspace(fileList) {
    const files = Array.from(fileList || []);
    const rootName = files.length ? files[0].webkitRelativePath.split('/')[0] : 'folder';
    const index = new Map();
    for (const f of files) {
        const rel = f.webkitRelativePath.split('/').slice(1).join('/');
        if (rel) index.set(rel, f);
    }
    return {
        kind: 'snapshot',
        rootName,
        hasFile: (p) => index.has(p),
        listFiles: () => Array.from(index.keys()),
        readText: async (p) => {
            const f = index.get(p);
            if (!f) throw new Error(`Not in workspace: ${p}`);
            return f.text();
        },
        readBlob: async (p) => {
            const f = index.get(p);
            if (!f) throw new Error(`Not in workspace: ${p}`);
            return f;
        },
    };
}

export async function indexDirectoryHandle(dirHandle, prefix = '', index = new Map(), depth = 0) {
    if (depth > 32) return index; // guard against deep / looping trees
    for await (const [name, handle] of dirHandle.entries()) {
        const rel = prefix ? `${prefix}/${name}` : name;
        if (handle.kind === 'file') index.set(rel, handle);
        else if (handle.kind === 'directory') await indexDirectoryHandle(handle, rel, index, depth + 1);
    }
    return index;
}

export async function createFsaWorkspace(dirHandle) {
    const index = await indexDirectoryHandle(dirHandle);
    return {
        kind: 'fsa',
        rootName: dirHandle.name,
        handle: dirHandle,
        hasFile: (p) => index.has(p),
        listFiles: () => Array.from(index.keys()),
        readText: async (p) => {
            const h = index.get(p);
            if (!h) throw new Error(`Not in workspace: ${p}`);
            return (await h.getFile()).text();
        },
        readBlob: async (p) => {
            const h = index.get(p);
            if (!h) throw new Error(`Not in workspace: ${p}`);
            return h.getFile();
        },
    };
}

export function pickEntryFile(paths) {
    const lower = new Map((paths || []).map((p) => [p.toLowerCase(), p]));
    for (const cand of ['readme.md', 'readme.markdown', 'index.md']) {
        if (lower.has(cand)) return lower.get(cand);
    }
    const md = (paths || [])
        .filter(isMarkdownPath)
        .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
    return md[0] || null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- src/lib/workspace.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/workspace.js src/lib/workspace.test.js
git commit -m "feat: add Workspace abstraction (FSA + snapshot backends)"
```

---

### Task 3: IndexedDB v4 — workspace persistence

**Files:**
- Modify: `src/db.js`
- Test: `src/lib/db.workspace.test.js`

**Interfaces:**
- Consumes: existing `openDB` upgrade chain in `src/db.js` (currently `DB_VERSION = 3`).
- Produces:
  - `saveWorkspaceState({ rootName, handle, lastPath }): Promise<boolean>`
  - `getWorkspaceState(): Promise<{ id:'last', rootName, handle?, lastPath }|null>`
  - `clearWorkspaceState(): Promise<boolean>`
  - New object store `workspaces` (keyPath `id`), single record id `'last'`.

- [ ] **Step 1: Write the failing test**

`src/lib/db.workspace.test.js`:
```js
import { describe, it, expect, beforeEach } from 'vitest';
import 'fake-indexeddb/auto';
import { saveWorkspaceState, getWorkspaceState, clearWorkspaceState } from '../db';

describe('workspace persistence', () => {
    beforeEach(async () => { await clearWorkspaceState(); });

    it('saves and reads back the last workspace state', async () => {
        await saveWorkspaceState({ rootName: 'vault', handle: null, lastPath: 'sub/b.md' });
        const got = await getWorkspaceState();
        expect(got.rootName).toBe('vault');
        expect(got.lastPath).toBe('sub/b.md');
    });

    it('clears state', async () => {
        await saveWorkspaceState({ rootName: 'v', handle: null, lastPath: 'a.md' });
        await clearWorkspaceState();
        expect(await getWorkspaceState()).toBe(null);
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/lib/db.workspace.test.js`
Expected: FAIL (`saveWorkspaceState` is not exported).

- [ ] **Step 3: Bump the DB version and add the store**

In `src/db.js`, change `const DB_VERSION = 3;` to `const DB_VERSION = 4;` and add a store constant near the others:
```js
const WORKSPACE_STORE = 'workspaces';
```
Inside `openDB`'s `onupgradeneeded`, after the v2→v3 block, add:
```js
// v3 → v4: workspace persistence (one record, id 'last')
if (event.oldVersion < 4) {
    if (!db.objectStoreNames.contains(WORKSPACE_STORE)) {
        db.createObjectStore(WORKSPACE_STORE, { keyPath: 'id' });
    }
}
```

- [ ] **Step 4: Add the three exported functions**

Append to `src/db.js`:
```js
// =====================================================================
// WORKSPACE STATE (single record, id 'last')
//   { id:'last', rootName, handle?, lastPath }
// `handle` is a structured-clonable FileSystemDirectoryHandle (FSA only);
// snapshot workspaces persist rootName + lastPath without a handle.
// =====================================================================

export const saveWorkspaceState = async ({ rootName, handle = null, lastPath = null }) => {
    try {
        const db = await openDB();
        const tx = db.transaction(WORKSPACE_STORE, 'readwrite');
        const store = tx.objectStore(WORKSPACE_STORE);
        await new Promise((resolve, reject) => {
            const req = store.put({ id: 'last', rootName, handle, lastPath });
            req.onsuccess = resolve;
            req.onerror = () => reject(req.error);
        });
        db.close();
        return true;
    } catch (e) {
        console.error('Failed to save workspace state:', e);
        return false;
    }
};

export const getWorkspaceState = async () => {
    try {
        const db = await openDB();
        const tx = db.transaction(WORKSPACE_STORE, 'readonly');
        const store = tx.objectStore(WORKSPACE_STORE);
        const result = await new Promise((resolve, reject) => {
            const req = store.get('last');
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
        db.close();
        return result || null;
    } catch (e) {
        console.error('Failed to read workspace state:', e);
        return null;
    }
};

export const clearWorkspaceState = async () => {
    try {
        const db = await openDB();
        const tx = db.transaction(WORKSPACE_STORE, 'readwrite');
        const store = tx.objectStore(WORKSPACE_STORE);
        await new Promise((resolve, reject) => {
            const req = store.delete('last');
            req.onsuccess = resolve;
            req.onerror = () => reject(req.error);
        });
        db.close();
        return true;
    } catch (e) {
        console.error('Failed to clear workspace state:', e);
        return false;
    }
};
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test:run -- src/lib/db.workspace.test.js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/db.js src/lib/db.workspace.test.js
git commit -m "feat: persist workspace state in IndexedDB (v4 store)"
```

---

### Task 4: `WorkspaceContext` — history + navigation

**Files:**
- Create: `src/lib/WorkspaceContext.jsx`
- Test: `src/lib/WorkspaceContext.test.jsx`

**Interfaces:**
- Consumes: `Workspace` (Task 2).
- Produces:
  - `<WorkspaceProvider workspace initialPath onOpenDoc>{children}</WorkspaceProvider>` where `onOpenDoc(path: string, opts: { anchor: string|null }): void` performs the actual document load (wired by App in Task 7). The provider calls `onOpenDoc` for the initial path on mount and on every navigation.
  - `useWorkspace(): { workspace, currentPath, navigate(path, anchor?), goBack(), goForward(), canGoBack, canGoForward }`
  - When no provider is mounted, `useWorkspace()` returns `{ workspace: null }` (single-file mode) so renderer components degrade gracefully.

- [ ] **Step 1: Write the failing test**

`src/lib/WorkspaceContext.test.jsx`:
```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { WorkspaceProvider, useWorkspace } from './WorkspaceContext';

const fakeWs = { rootName: 'v', hasFile: () => true };

function Probe() {
    const { currentPath, navigate, goBack, goForward, canGoBack, canGoForward } = useWorkspace();
    return (
        <div>
            <span data-testid="path">{currentPath}</span>
            <span data-testid="back">{String(canGoBack)}</span>
            <span data-testid="fwd">{String(canGoForward)}</span>
            <button onClick={() => navigate('b.md')}>nav-b</button>
            <button onClick={() => navigate('c.md')}>nav-c</button>
            <button onClick={goBack}>back</button>
            <button onClick={goForward}>fwd</button>
        </div>
    );
}

describe('WorkspaceProvider', () => {
    it('opens the initial path and tracks Back/Forward history', () => {
        const onOpenDoc = vi.fn();
        render(
            <WorkspaceProvider workspace={fakeWs} initialPath="a.md" onOpenDoc={onOpenDoc}>
                <Probe />
            </WorkspaceProvider>,
        );
        expect(onOpenDoc).toHaveBeenCalledWith('a.md', { anchor: null });
        expect(screen.getByTestId('path').textContent).toBe('a.md');
        expect(screen.getByTestId('back').textContent).toBe('false');

        act(() => { screen.getByText('nav-b').click(); });
        expect(screen.getByTestId('path').textContent).toBe('b.md');
        expect(screen.getByTestId('back').textContent).toBe('true');

        act(() => { screen.getByText('back').click(); });
        expect(screen.getByTestId('path').textContent).toBe('a.md');
        expect(screen.getByTestId('fwd').textContent).toBe('true');

        // Navigating after going back truncates forward history.
        act(() => { screen.getByText('nav-c').click(); });
        expect(screen.getByTestId('path').textContent).toBe('c.md');
        expect(screen.getByTestId('fwd').textContent).toBe('false');
    });

    it('returns workspace:null without a provider', () => {
        function Bare() {
            const { workspace } = useWorkspace();
            return <span>{String(workspace)}</span>;
        }
        render(<Bare />);
        expect(screen.getByText('null')).toBeInTheDocument();
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/lib/WorkspaceContext.test.jsx`
Expected: FAIL (cannot find module `./WorkspaceContext`).

- [ ] **Step 3: Write the implementation**

`src/lib/WorkspaceContext.jsx`:
```jsx
import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';

const WorkspaceContext = createContext({ workspace: null });

export function useWorkspace() {
    return useContext(WorkspaceContext);
}

export function WorkspaceProvider({ workspace, initialPath, onOpenDoc, children }) {
    // history is an array of paths; pointer indexes the active entry.
    const [history, setHistory] = useState(() => (initialPath ? [initialPath] : []));
    const [pointer, setPointer] = useState(initialPath ? 0 : -1);
    const onOpenRef = useRef(onOpenDoc);
    onOpenRef.current = onOpenDoc;

    // Open the initial document once per workspace/initialPath.
    const openedKey = useRef(null);
    useEffect(() => {
        const key = `${workspace?.rootName}::${initialPath}`;
        if (workspace && initialPath && openedKey.current !== key) {
            openedKey.current = key;
            setHistory([initialPath]);
            setPointer(0);
            onOpenRef.current?.(initialPath, { anchor: null });
        }
    }, [workspace, initialPath]);

    const value = useMemo(() => {
        const currentPath = pointer >= 0 ? history[pointer] : null;
        return {
            workspace,
            currentPath,
            canGoBack: pointer > 0,
            canGoForward: pointer >= 0 && pointer < history.length - 1,
            navigate: (path, anchor = null) => {
                setHistory((h) => [...h.slice(0, pointer + 1), path]);
                setPointer((p) => p + 1);
                onOpenRef.current?.(path, { anchor });
            },
            goBack: () => {
                if (pointer <= 0) return;
                const next = pointer - 1;
                setPointer(next);
                onOpenRef.current?.(history[next], { anchor: null });
            },
            goForward: () => {
                if (pointer >= history.length - 1) return;
                const next = pointer + 1;
                setPointer(next);
                onOpenRef.current?.(history[next], { anchor: null });
            },
        };
    }, [workspace, history, pointer]);

    return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- src/lib/WorkspaceContext.test.jsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/WorkspaceContext.jsx src/lib/WorkspaceContext.test.jsx
git commit -m "feat: add WorkspaceProvider with Back/Forward history"
```

---

### Task 5: `WorkspaceLink` + `WorkspaceImage`

**Files:**
- Create: `src/components/WorkspaceLink.jsx`
- Test: `src/components/WorkspaceLink.test.jsx`

**Interfaces:**
- Consumes: `useWorkspace` (Task 4), `resolvePath`/`dirname` (Task 1), `isNavigable` (Task 2).
- Produces:
  - `<WorkspaceLink href children />` — renders `<a>`; intercepts in-workspace `.md`/`.txt` and anchor links, otherwise external `target="_blank"`.
  - `<WorkspaceImage src alt />` — resolves relative `src` from the workspace to an object URL; external/`data:` srcs pass through.
  - On a missing in-workspace target, calls `onMissing?.(path)` from context if present (App supplies a toast); falls back to a no-op link.

- [ ] **Step 1: Write the failing test**

`src/components/WorkspaceLink.test.jsx`:
```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorkspaceLink } from './WorkspaceLink';
import * as ctx from '../lib/WorkspaceContext';

function mockWorkspace(over = {}) {
    vi.spyOn(ctx, 'useWorkspace').mockReturnValue({
        workspace: { hasFile: (p) => ['a.md', 'sub/b.md'].includes(p) },
        currentPath: 'a.md',
        navigate: vi.fn(),
        ...over,
    });
}

describe('WorkspaceLink', () => {
    it('navigates for an in-workspace relative .md link', () => {
        const navigate = vi.fn();
        mockWorkspace({ navigate });
        render(<WorkspaceLink href="./sub/b.md">B</WorkspaceLink>);
        fireEvent.click(screen.getByText('B'));
        expect(navigate).toHaveBeenCalledWith('sub/b.md', null);
    });

    it('renders external links with target=_blank and does not navigate', () => {
        const navigate = vi.fn();
        mockWorkspace({ navigate });
        render(<WorkspaceLink href="https://x.com">X</WorkspaceLink>);
        const a = screen.getByText('X');
        expect(a).toHaveAttribute('target', '_blank');
        fireEvent.click(a);
        expect(navigate).not.toHaveBeenCalled();
    });

    it('in single-file mode (no workspace) behaves like a plain external link', () => {
        vi.spyOn(ctx, 'useWorkspace').mockReturnValue({ workspace: null });
        render(<WorkspaceLink href="./b.md">B</WorkspaceLink>);
        expect(screen.getByText('B')).toHaveAttribute('target', '_blank');
    });

    it('renders a dangerous-scheme link inert (no href)', () => {
        const navigate = vi.fn();
        mockWorkspace({ navigate });
        render(<WorkspaceLink href="javascript:alert(1)">click</WorkspaceLink>);
        const el = screen.getByText('click');
        expect(el).not.toHaveAttribute('href');
        fireEvent.click(el);
        expect(navigate).not.toHaveBeenCalled();
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/components/WorkspaceLink.test.jsx`
Expected: FAIL (cannot find module `./WorkspaceLink`).

- [ ] **Step 3: Write the implementation**

`src/components/WorkspaceLink.jsx`:
```jsx
import { useEffect, useState } from 'react';
import { useWorkspace } from '../lib/WorkspaceContext';
import { resolvePath, dirname } from '../utils/resolvePath';
import { isNavigable } from '../lib/workspace';

const LINK_CLASS = 'text-blue-500 underline break-words';

function scrollToAnchor(anchor) {
    if (!anchor) return;
    const el = document.getElementById(anchor);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export function WorkspaceLink({ href, children, ...props }) {
    const ws = useWorkspace();
    const external = (
        <a {...props} href={href} target="_blank" rel="noopener noreferrer" className={LINK_CLASS}>
            {children}
        </a>
    );
    if (!ws?.workspace) return external; // single-file mode: unchanged behavior

    const r = resolvePath(dirname(ws.currentPath || ''), href);
    // Dangerous scheme (javascript:, data:, …) — drop the href entirely,
    // render inert text so it can never be clicked/navigated.
    if (r.kind === 'unsafe') return <span {...props}>{children}</span>;
    if (r.kind === 'external' || r.kind === 'none') return external;

    const onClick = (e) => {
        if (r.kind === 'anchor') {
            e.preventDefault();
            scrollToAnchor(r.anchor);
            return;
        }
        // r.kind === 'path'
        if (ws.workspace.hasFile(r.path) && isNavigable(r.path)) {
            e.preventDefault();
            ws.navigate(r.path, r.anchor);
        } else {
            e.preventDefault();
            ws.onMissing?.(r.path);
        }
    };

    return (
        <a {...props} href={href} onClick={onClick} className={LINK_CLASS}>
            {children}
        </a>
    );
}

export function WorkspaceImage({ src, alt, ...props }) {
    const ws = useWorkspace();
    const [url, setUrl] = useState(null);
    const relative = src && !/^([a-z]+:|\/\/|data:)/i.test(src);

    useEffect(() => {
        let revoked = null;
        if (ws?.workspace && relative) {
            const r = resolvePath(dirname(ws.currentPath || ''), src);
            if (r.kind === 'path' && ws.workspace.hasFile(r.path)) {
                ws.workspace.readBlob(r.path).then((blob) => {
                    revoked = URL.createObjectURL(blob);
                    setUrl(revoked);
                }).catch(() => setUrl(null));
            }
        }
        return () => { if (revoked) URL.revokeObjectURL(revoked); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [src, ws?.currentPath, ws?.workspace]);

    return <img {...props} alt={alt} src={url || src} className="max-w-full rounded-md my-3" />;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- src/components/WorkspaceLink.test.jsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/WorkspaceLink.jsx src/components/WorkspaceLink.test.jsx
git commit -m "feat: add WorkspaceLink + WorkspaceImage for in-folder navigation"
```

---

### Task 6: Wire renderer + surface hook loaders

**Files:**
- Modify: `src/components/MarkdownPageRenderer.jsx`
- Modify: `src/hooks/usePdfEngine.js:586` (the return object)

**Interfaces:**
- Consumes: `WorkspaceLink`/`WorkspaceImage` (Task 5).
- Produces: `usePdfEngine()` return now includes `loadMarkdownDocument` and `loadTextDocument` (already defined at `usePdfEngine.js:332` and `:348`); the Markdown renderer routes `a`/`img` through workspace-aware components and emits heading IDs via `rehype-slug`.

- [ ] **Step 1: Surface the loaders from the hook**

In `src/hooks/usePdfEngine.js`, find the `return {` block (around line 586) and add `loadMarkdownDocument,` and `loadTextDocument,` to the returned object (near `processFile,`).

- [ ] **Step 2: Add `rehype-slug` + workspace components to the renderer**

In `src/components/MarkdownPageRenderer.jsx`:
- Add imports at top:
```jsx
import rehypeSlug from 'rehype-slug';
import { WorkspaceLink, WorkspaceImage } from './WorkspaceLink';
```
- Replace the `a:` entry in `components` with:
```jsx
a: ({ children, href, ...props }) => (
    <WorkspaceLink {...props} href={href}>{children}</WorkspaceLink>
),
```
- Replace the `img:` entry with:
```jsx
img: ({ alt, src, ...props }) => <WorkspaceImage {...props} alt={alt} src={src} />,
```
- Add `rehypePlugins={[rehypeSlug]}` to the `<ReactMarkdown>` element so headings get `id`s:
```jsx
<ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSlug]} components={components}>
    {pageData.rawMarkdown}
</ReactMarkdown>
```

- [ ] **Step 3: Verify the build and existing tests still pass**

Run: `npm run build`
Expected: build succeeds (no missing import / syntax errors).
Run: `npm run test:run`
Expected: PASS (all prior tests green).

- [ ] **Step 4: Manual smoke (single-file regression)**

Run: `npm run dev`, open a normal single `.md` file (no folder). Confirm links still open in a new tab and images behave as before (no workspace = unchanged path).

- [ ] **Step 5: Commit**

```bash
git add src/components/MarkdownPageRenderer.jsx src/hooks/usePdfEngine.js
git commit -m "feat: route markdown links/images through workspace + add heading ids"
```

---

### Task 7: App integration — open folder, provider, onOpenDoc

**Files:**
- Modify: `src/App.jsx`

**Interfaces:**
- Consumes: `createFsaWorkspace`/`createSnapshotWorkspace`/`pickEntryFile`/`isMarkdownPath` (Task 2), `WorkspaceProvider` (Task 4), `saveWorkspaceState` (Task 3), `loadMarkdownDocument`/`loadTextDocument` (Task 6).
- Produces: an `openFolder()` handler, `workspace`/`workspaceEntryPath` state, an `onOpenDoc` callback, and the reader subtree wrapped in `WorkspaceProvider`.

- [ ] **Step 1: Add imports**

In `src/App.jsx`, add:
```jsx
import { WorkspaceProvider } from './lib/WorkspaceContext';
import { createFsaWorkspace, createSnapshotWorkspace, pickEntryFile, isMarkdownPath } from './lib/workspace';
import { saveWorkspaceState } from './db';
```

- [ ] **Step 2: Pull the loaders out of the engine**

In the `usePdfEngine` destructure (around `src/App.jsx:120`), add `loadMarkdownDocument, loadTextDocument,` to the destructured names.

- [ ] **Step 3: Add workspace state + a folder-input ref**

Near the other `useState` declarations in `App`:
```jsx
const [workspace, setWorkspace] = useState(null);
const [workspaceEntryPath, setWorkspaceEntryPath] = useState(null);
const folderInputRef = useRef(null);
```

- [ ] **Step 4: Add `onOpenDoc` + `openFolder` + a snapshot input handler**

```jsx
// Loads a workspace-relative file into the reader WITHOUT touching the
// 5-doc library (synthesized docs are workspace-scoped). Reused for initial
// open, link navigation, and Back/Forward.
const onOpenDoc = useCallback(async (path, { anchor } = {}) => {
    if (!workspace) return;
    try {
        const text = await workspace.readText(path);
        const name = path.split('/').pop();
        if (isMarkdownPath(path)) loadMarkdownDocument(text, name);
        else loadTextDocument(text, name);
        saveWorkspaceState({ rootName: workspace.rootName, handle: workspace.handle || null, lastPath: path });
        if (anchor) {
            setTimeout(() => {
                const el = document.getElementById(anchor);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 100);
        }
    } catch {
        showToast('Could not open that file from the folder.', 3000);
    }
}, [workspace, loadMarkdownDocument, loadTextDocument, showToast]);

const adoptWorkspace = useCallback((ws) => {
    const entry = pickEntryFile(ws.listFiles());
    if (!entry) {
        showToast('No Markdown files found in that folder.', 4000);
        return;
    }
    setWorkspace(ws);
    setWorkspaceEntryPath(entry);
    setViewMode('reader');
}, [showToast, setViewMode]);

const openFolder = useCallback(async () => {
    if (typeof window !== 'undefined' && window.showDirectoryPicker) {
        try {
            const handle = await window.showDirectoryPicker({ mode: 'read' });
            adoptWorkspace(await createFsaWorkspace(handle));
        } catch (e) {
            if (e?.name !== 'AbortError') showToast('Could not open folder.', 3000);
        }
    } else {
        folderInputRef.current?.click(); // webkitdirectory fallback
    }
}, [adoptWorkspace, showToast]);

const handleFolderInput = useCallback((e) => {
    const files = e.target.files;
    if (files && files.length) adoptWorkspace(createSnapshotWorkspace(files));
    e.target.value = '';
}, [adoptWorkspace]);
```
(If `showToast`/`setViewMode`/`useCallback`/`useRef` aren't already imported/in scope, add them — `showToast` and `setViewMode` already exist in this component.)

- [ ] **Step 5: Render the hidden folder input**

Next to the existing hidden file input in `App`'s JSX, add:
```jsx
<input
    ref={folderInputRef}
    type="file"
    webkitdirectory=""
    directory=""
    multiple
    style={{ display: 'none' }}
    onChange={handleFolderInput}
/>
```

- [ ] **Step 6: Wrap the reader subtree in `WorkspaceProvider`**

Wrap the existing reader view (the `PdfViewer`/markdown reader subtree) so the renderer can consume context:
```jsx
<WorkspaceProvider workspace={workspace} initialPath={workspaceEntryPath} onOpenDoc={onOpenDoc}>
    {/* existing reader subtree (PdfViewer etc.) */}
</WorkspaceProvider>
```
Pass `openFolder` down to `WelcomeScreen` and `Header` (props added in Task 8).

- [ ] **Step 7: Verify build + tests**

Run: `npm run build` → succeeds.
Run: `npm run test:run` → PASS.

- [ ] **Step 8: Manual integration test (Chromium)**

Run `npm run dev` in Chrome. Trigger `openFolder` (temporarily wire it to an existing button if Task 8 isn't done yet), pick a folder containing `README.md` linking to `./sub/b.md`. Confirm: README opens, clicking the link loads `sub/b.md` in the reader with TTS available.

- [ ] **Step 9: Commit**

```bash
git add src/App.jsx
git commit -m "feat: open folder as workspace and navigate links via reader"
```

---

### Task 8: UI entry points — WelcomeScreen, Header badge, Back/Forward

**Files:**
- Modify: `src/components/WelcomeScreen.jsx`
- Modify: `src/components/Header.jsx`
- Modify: `src/App.jsx` (pass props; add Back/Forward control near the reader toolbar)

**Interfaces:**
- Consumes: `openFolder` (Task 7), `useWorkspace` (Task 4).
- Produces: an "Open folder" button on the welcome screen, a workspace-name badge + close button in the header, and Back/Forward buttons shown only when a workspace is active.

- [ ] **Step 1: Add "Open folder" to WelcomeScreen**

In `src/components/WelcomeScreen.jsx`, accept an `openFolder` prop and add a button beside the existing "Open file" trigger:
```jsx
<button
    type="button"
    onClick={openFolder}
    className="px-4 py-2 rounded-lg border text-sm font-medium hover:bg-black/5"
>
    Open folder
</button>
```
Pass `openFolder={openFolder}` where `WelcomeScreen` is rendered in `App.jsx`.

- [ ] **Step 2: Add a workspace badge to the Header**

In `src/components/Header.jsx`, accept `workspaceName` and `onCloseWorkspace` props; when `workspaceName` is set, render a small badge:
```jsx
{workspaceName && (
    <div className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-blue-500/10 text-blue-500">
        <span className="truncate max-w-[140px]">📁 {workspaceName}</span>
        <button onClick={onCloseWorkspace} title="Close folder" className="hover:opacity-70">×</button>
    </div>
)}
```
In `App.jsx`, pass `workspaceName={workspace?.rootName}` and an `onCloseWorkspace` that does `setWorkspace(null); setWorkspaceEntryPath(null); clearWorkspaceState();` (import `clearWorkspaceState`).

- [ ] **Step 3: Add Back/Forward controls (workspace-only)**

Create a tiny presentational control rendered inside the `WorkspaceProvider` subtree (so it can call `useWorkspace`). Add to `src/components/WorkspaceLink.jsx`:
```jsx
import { ChevronLeft, ChevronRight } from 'lucide-react';

export function WorkspaceNav() {
    const ws = useWorkspace();
    if (!ws?.workspace) return null;
    const btn = 'p-1 rounded disabled:opacity-30 hover:bg-black/10';
    return (
        <div className="flex items-center gap-1">
            <button className={btn} onClick={ws.goBack} disabled={!ws.canGoBack} title="Back"><ChevronLeft size={18} /></button>
            <button className={btn} onClick={ws.goForward} disabled={!ws.canGoForward} title="Forward"><ChevronRight size={18} /></button>
        </div>
    );
}
```
Render `<WorkspaceNav />` in the reader toolbar area inside the provider subtree in `App.jsx`.

- [ ] **Step 4: Verify build + tests + lint**

Run: `npm run build` → succeeds.
Run: `npm run test:run` → PASS.
Run: `npm run lint` → no new errors.

- [ ] **Step 5: Manual test**

`npm run dev`: from the welcome screen click "Open folder", navigate via a link, then use Back/Forward; close the folder via the header badge and confirm the app returns to single-file mode.

- [ ] **Step 6: Commit**

```bash
git add src/components/WelcomeScreen.jsx src/components/Header.jsx src/components/WorkspaceLink.jsx src/App.jsx
git commit -m "feat: workspace UI — open folder, header badge, Back/Forward"
```

---

### Task 9: Persistence restore on load

**Files:**
- Modify: `src/App.jsx`

**Interfaces:**
- Consumes: `getWorkspaceState` (Task 3), `createFsaWorkspace`/`createSnapshotWorkspace` (Task 2).
- Produces: on app start, silently restores an FSA workspace if permission is still granted; otherwise exposes a one-click "Reconnect folder" (FSA) or "Re-open folder" (snapshot, via `openFolder`). The restored `lastPath` becomes the initial path.

- [ ] **Step 1: Add a restore effect**

In `src/App.jsx`, after `isLibLoaded` is true, add:
```jsx
const [reconnect, setReconnect] = useState(null); // { rootName } | null

useEffect(() => {
    if (!isLibLoaded) return;
    (async () => {
        const saved = await getWorkspaceState();
        if (!saved) return;
        if (saved.handle && saved.handle.queryPermission) {
            const perm = await saved.handle.queryPermission({ mode: 'read' });
            if (perm === 'granted') {
                const ws = await createFsaWorkspace(saved.handle);
                setWorkspace(ws);
                setWorkspaceEntryPath(saved.lastPath || pickEntryFile(ws.listFiles()));
            } else {
                setReconnect({ rootName: saved.rootName }); // needs a user gesture
            }
        } else {
            setReconnect({ rootName: saved.rootName }); // snapshot: must re-pick
        }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
}, [isLibLoaded]);
```
(Import `getWorkspaceState`, `createFsaWorkspace`, `pickEntryFile`.)

- [ ] **Step 2: Add a reconnect handler**

```jsx
const reconnectFolder = useCallback(async () => {
    const saved = await getWorkspaceState();
    if (saved?.handle?.requestPermission) {
        const perm = await saved.handle.requestPermission({ mode: 'read' });
        if (perm === 'granted') {
            const ws = await createFsaWorkspace(saved.handle);
            setWorkspace(ws);
            setWorkspaceEntryPath(saved.lastPath || pickEntryFile(ws.listFiles()));
            setReconnect(null);
            return;
        }
    }
    openFolder(); // snapshot or denied: full re-pick
}, [openFolder]);
```

- [ ] **Step 3: Render the reconnect prompt**

When `reconnect` is set and no workspace is active, show a small banner with a button calling `reconnectFolder`:
```jsx
{reconnect && !workspace && (
    <button onClick={reconnectFolder} className="text-sm underline text-blue-500">
        Reconnect folder “{reconnect.rootName}”
    </button>
)}
```

- [ ] **Step 4: Verify build + tests**

Run: `npm run build` → succeeds.
Run: `npm run test:run` → PASS.

- [ ] **Step 5: Manual test (Chromium)**

Open a folder, navigate to a sub-file, reload the page. Expected: in Chrome with permission still granted, the workspace restores to `lastPath`; if the browser drops permission, the "Reconnect folder" button appears and one click restores it.

- [ ] **Step 6: Commit**

```bash
git add src/App.jsx
git commit -m "feat: restore workspace on load (FSA re-permission / snapshot re-pick)"
```

---

## Self-Review

**Spec coverage:**
- Workspace abstraction + FSA/snapshot backends → Task 2 ✓
- `WorkspaceContext` + history → Task 4 ✓
- `resolvePath` pure util → Task 1 ✓
- Link interception (external/anchor/relative/missing) → Task 5 ✓
- `rehype-slug` heading IDs + anchor scroll → Task 5 (scroll) + Task 6 (slugs) ✓
- Relative image resolution w/ revoked object URLs → Task 5 (`WorkspaceImage`) ✓
- Active-doc swap via existing loaders, no library write → Task 6 (surface) + Task 7 (`onOpenDoc`) ✓
- Back/Forward toolbar (workspace-only) → Task 8 ✓
- Entry-file rule → Task 2 (`pickEntryFile`) ✓
- IndexedDB v3→v4 persistence → Task 3 ✓
- FSA re-permission / snapshot re-pick restore → Task 9 ✓
- Open-folder entry points (WelcomeScreen, Header badge) → Task 8 ✓
- Non-goals (PDF links, file-tree, docling reader, backend) → not implemented ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code. ✓

**Type/name consistency:** `loadMarkdownDocument`/`loadTextDocument` (defined `usePdfEngine.js:332,348`; surfaced Task 6; consumed Task 7); `onOpenDoc(path, { anchor })` shape consistent across Tasks 4/7; `pickEntryFile`, `createFsaWorkspace`, `createSnapshotWorkspace`, `isMarkdownPath`, `isNavigable` consistent across Tasks 2/5/7; `saveWorkspaceState`/`getWorkspaceState`/`clearWorkspaceState` consistent across Tasks 3/7/8/9; `useWorkspace` fields (`navigate`, `goBack`, `goForward`, `canGoBack`, `canGoForward`, `currentPath`, `workspace`) consistent across Tasks 4/5/8. ✓

**Note for implementer:** Tasks 7–9 touch `App.jsx`, a large file. Match its existing `useState`/`useCallback`/`useRef` placement and JSX structure; the snippets specify *what* to add, not exact line numbers — locate the analogous existing code (hidden file input, `WelcomeScreen`/`Header` render sites, reader subtree) and insert alongside.
