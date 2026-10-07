import { describe, it, expect, beforeEach } from 'vitest';
import 'fake-indexeddb/auto';
import {
    setLibraryOwner,
    saveSession, getSession, getRecentSessions, deleteSession,
    saveWorkspaceState, getWorkspaceState, clearWorkspaceState,
} from './db';
import { saveReadingProgress, loadReadingProgress } from './hooks/usePersistedState';

// v2.2 Task A: the other per-browser stores follow the same owner as "Your
// Library" (see db.library-owner.test.js): scoped to the signed-in user,
// nothing when no user is known, pre-release data claimed once.

const resetDb = () => new Promise((resolve, reject) => {
    const req = indexedDB.deleteDatabase('neural-pdf-library');
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
});

const claim = (id) => setLibraryOwner(id, { claimLegacy: true });
const session = (id) => ({ id, title: `chat ${id}`, model: 'm', createdAt: 1, messages: [{ role: 'user', content: 'hi' }] });

// A pre-release database (v5 shape): chat sessions and the workspace record
// written by the old code, with no owner at all.
const seedLegacy = () => new Promise((resolve, reject) => {
    const req = indexedDB.open('neural-pdf-library', 5);
    req.onupgradeneeded = (event) => {
        const db = event.target.result;
        const books = db.createObjectStore('books');
        books.createIndex('lastOpened', 'lastOpened', { unique: false });
        books.createIndex('ownerId', 'ownerId', { unique: false });
        const sessions = db.createObjectStore('chat_sessions', { keyPath: 'id' });
        sessions.createIndex('updatedAt', 'updatedAt', { unique: false });
        sessions.put({ ...session('old-chat'), updatedAt: 5 });
        db.createObjectStore('workspaces', { keyPath: 'id' }).put({ id: 'last', rootName: 'notes', handle: null, lastPath: 'a.md' });
    };
    req.onsuccess = () => { req.result.close(); resolve(); };
    req.onerror = () => reject(req.error);
});

beforeEach(async () => {
    await resetDb();
    localStorage.clear();
    await setLibraryOwner(null);
});

describe('browser-only chats', () => {
    it('each user sees only their own; another user can neither read nor delete them', async () => {
        await setLibraryOwner('userA');
        await saveSession(session('a1'));
        await setLibraryOwner('userB');
        expect(await getRecentSessions()).toEqual([]);
        expect(await getSession('a1')).toBeNull();
        expect(await deleteSession('a1')).toBe(false);
        await setLibraryOwner('userA');
        expect((await getRecentSessions()).map((s) => s.id)).toEqual(['a1']);
        expect((await getSession('a1')).title).toBe('chat a1');
    });

    it('with no user known, nothing is read and nothing is saved', async () => {
        await setLibraryOwner('userA');
        await saveSession(session('a1'));
        await setLibraryOwner(null);
        expect(await getRecentSessions()).toEqual([]);
        expect(await getSession('a1')).toBeNull();
        expect(await saveSession(session('x'))).toBe(false);
    });

    it('pre-release chats go to the first user who signs in, once', async () => {
        await seedLegacy();
        await claim('userA');
        expect((await getRecentSessions()).map((s) => s.id)).toEqual(['old-chat']);
        await claim('userB');
        expect(await getRecentSessions()).toEqual([]);
    });
});

describe('the last workspace folder', () => {
    it('is per user, and a user with none gets none', async () => {
        await setLibraryOwner('userA');
        await saveWorkspaceState({ rootName: 'A-notes', lastPath: 'x.md' });
        await setLibraryOwner('userB');
        expect(await getWorkspaceState()).toBeNull();
        await saveWorkspaceState({ rootName: 'B-notes', lastPath: 'y.md' });
        await clearWorkspaceState();
        await setLibraryOwner('userA');
        expect((await getWorkspaceState()).rootName).toBe('A-notes');
    });

    it('with no user known, nothing is read and nothing is saved', async () => {
        await setLibraryOwner('userA');
        await saveWorkspaceState({ rootName: 'A-notes', lastPath: 'x.md' });
        await setLibraryOwner(null);
        expect(await getWorkspaceState()).toBeNull();
        expect(await saveWorkspaceState({ rootName: 'z' })).toBe(false);
    });

    it('the pre-release workspace goes to the first user who signs in, once', async () => {
        await seedLegacy();
        await claim('userA');
        expect(await getWorkspaceState()).toMatchObject({ rootName: 'notes', lastPath: 'a.md' });
        await claim('userB');
        expect(await getWorkspaceState()).toBeNull();
    });
});

describe('reading positions', () => {
    it('are per user', async () => {
        await setLibraryOwner('userA');
        saveReadingProgress('paper.pdf', 7, 3);
        await setLibraryOwner('userB');
        expect(loadReadingProgress('paper.pdf')).toBeNull();
        await setLibraryOwner('userA');
        expect(loadReadingProgress('paper.pdf')).toMatchObject({ page: 7, sentenceIndex: 3 });
    });

    it('with no user known, nothing is restored or saved', async () => {
        await setLibraryOwner(null);
        saveReadingProgress('paper.pdf', 7, 3);
        expect(loadReadingProgress('paper.pdf')).toBeNull();
        expect(Object.keys(localStorage).filter((k) => k.includes('progress'))).toEqual([]);
    });

    it('pre-release positions go to the first user who signs in, once', async () => {
        localStorage.setItem('neural-pdf-progress-paper.pdf', JSON.stringify({ page: 4, sentenceIndex: 1, timestamp: Date.now() }));
        await claim('userA');
        expect(loadReadingProgress('paper.pdf')).toMatchObject({ page: 4 });
        expect(localStorage.getItem('neural-pdf-progress-paper.pdf')).toBeNull();
        await claim('userB');
        expect(loadReadingProgress('paper.pdf')).toBeNull();
    });
});

describe('chat cap', () => {
    it('one user\'s chats never evict another\'s', async () => {
        await setLibraryOwner('userA');
        await saveSession(session('a-keep'));
        await setLibraryOwner('userB');
        for (let i = 0; i < 51; i++) await saveSession(session(`b${i}`));
        expect(await getRecentSessions()).toHaveLength(50);
        await setLibraryOwner('userA');
        expect((await getRecentSessions()).map((s) => s.id)).toEqual(['a-keep']);
    });
});
