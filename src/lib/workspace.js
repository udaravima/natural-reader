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
