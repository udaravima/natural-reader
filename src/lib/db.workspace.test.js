import { describe, it, expect, beforeEach } from 'vitest';
import 'fake-indexeddb/auto';
import { saveWorkspaceState, getWorkspaceState, clearWorkspaceState, setLibraryOwner } from '../db';

describe('workspace persistence', () => {
    // The workspace belongs to the signed-in user (db.browser-state-owner.test.js).
    beforeEach(async () => { await setLibraryOwner('reader'); await clearWorkspaceState(); });

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
