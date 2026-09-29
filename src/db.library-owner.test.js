import { describe, it, expect, beforeEach } from 'vitest';
import 'fake-indexeddb/auto';
import {
    saveBook,
    getBook,
    getRecentBooks,
    deleteBook,
    updateBookMeta,
    setLibraryOwner,
} from './db';

// Every db.js call opens a *new* connection (`indexedDB.open(DB_NAME, DB_VERSION)`)
// and closes it when done, so the cleanest reset between tests is to delete the
// whole database — no dangling connections can block it.
const resetDb = () => new Promise((resolve, reject) => {
    const req = indexedDB.deleteDatabase('neural-pdf-library');
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
});

const makeFile = (name, text) => new File([text], name, { type: 'text/plain' });

// Simulates a real existing v4 install: books keyed in-line by `fileName`,
// no `ownerId` field at all. Opens the DB at a version *older* than the
// current DB_VERSION so db.js's own next open triggers onupgradeneeded.
const seedLegacyV4Db = (records = [{ fileName: 'old.pdf', text: 'ancient bytes' }]) => new Promise((resolve, reject) => {
    const req = indexedDB.open('neural-pdf-library', 4);
    req.onupgradeneeded = (event) => {
        const db = event.target.result;
        const store = db.createObjectStore('books', { keyPath: 'fileName' });
        store.createIndex('lastOpened', 'lastOpened', { unique: false });
        for (const r of records) {
            store.put({
                fileName: r.fileName,
                data: new TextEncoder().encode(r.text).buffer,
                size: r.text.length,
                fileType: 'pdf',
                lastOpened: 123,
            });
        }
    };
    req.onsuccess = () => { req.result.close(); resolve(); };
    req.onerror = () => reject(req.error);
});

beforeEach(async () => {
    await resetDb();
    await setLibraryOwner(null); // reset module-level owner state between tests
});

describe('library ownership — two users on one browser', () => {
    it('each user sees only their own saved books', async () => {
        await setLibraryOwner('userA');
        await saveBook(makeFile('report.pdf', 'A content'), { page: 1, sentenceIndex: -1 });

        await setLibraryOwner('userB');
        await saveBook(makeFile('report.pdf', 'B content'), { page: 1, sentenceIndex: -1 });

        const booksAsB = await getRecentBooks();
        expect(booksAsB).toHaveLength(1);
        expect(booksAsB[0].fileName).toBe('report.pdf');

        const bBook = await getBook('report.pdf');
        expect(new TextDecoder().decode(bBook.data)).toBe('B content');

        await setLibraryOwner('userA');
        const booksAsA = await getRecentBooks();
        expect(booksAsA).toHaveLength(1);

        const aBook = await getBook('report.pdf');
        expect(new TextDecoder().decode(aBook.data)).toBe('A content');
    });

    it('getBook returns nothing for a record owned by someone else (as if absent)', async () => {
        await setLibraryOwner('userA');
        await saveBook(makeFile('only-a.pdf', 'secret'), {});

        await setLibraryOwner('userB');
        expect(await getBook('only-a.pdf')).toBeNull();
        expect(await getRecentBooks()).toHaveLength(0);
    });

    it('deleteBook and updateBookMeta only ever touch the caller\'s own record', async () => {
        await setLibraryOwner('userA');
        await saveBook(makeFile('shared-name.pdf', 'A data'), {});

        await setLibraryOwner('userB');
        await saveBook(makeFile('shared-name.pdf', 'B data'), {});

        // userB deleting "shared-name.pdf" must not touch userA's copy.
        await deleteBook('shared-name.pdf');
        expect(await getRecentBooks()).toHaveLength(0);

        await setLibraryOwner('userA');
        const aBooks = await getRecentBooks();
        expect(aBooks).toHaveLength(1);
        expect(new TextDecoder().decode((await getBook('shared-name.pdf')).data)).toBe('A data');

        // userB updating a name it doesn't own is a no-op (record not found).
        await setLibraryOwner('userB');
        expect(await updateBookMeta('shared-name.pdf', { page: 9 })).toBe(false);
    });

    it('the 5-book cap applies per owner, not across all owners combined', async () => {
        await setLibraryOwner('userA');
        for (let i = 0; i < 6; i++) {
            await saveBook(makeFile(`a${i}.pdf`, `content${i}`), {});
        }

        await setLibraryOwner('userB');
        await saveBook(makeFile('b0.pdf', 'b'), {});

        await setLibraryOwner('userA');
        const aBooks = await getRecentBooks();
        expect(aBooks).toHaveLength(5); // oldest of the 6 trimmed

        await setLibraryOwner('userB');
        const bBooks = await getRecentBooks();
        expect(bBooks).toHaveLength(1); // untouched by userA's cap
    });
});

const claim = (id) => setLibraryOwner(id, { claimLegacy: true });

describe('library ownership — legacy (pre-release) records are claimed once', () => {
    it('the first signed-in user claims them; a later user never sees them', async () => {
        await seedLegacyV4Db([{ fileName: 'legacy.pdf', text: 'old data' }]);

        await claim('userA');
        expect((await getRecentBooks()).map((b) => b.fileName)).toEqual(['legacy.pdf']);

        await claim('userB');
        expect(await getRecentBooks()).toHaveLength(0);

        // Claiming again is a no-op: no error, no duplicate.
        await claim('userA');
        expect(await getRecentBooks()).toHaveLength(1);
    });

    it('only a claim takes them — setting an owner without claimLegacy leaves them unclaimed', async () => {
        await seedLegacyV4Db([{ fileName: 'legacy.pdf', text: 'old data' }]);

        await setLibraryOwner('userA');
        expect(await getRecentBooks()).toHaveLength(0);

        await claim('userB');
        expect(await getRecentBooks()).toHaveLength(1);
    });

    it('the "local" fallback (/v1/auth/me failed) does not take them from the real user', async () => {
        await seedLegacyV4Db([{ fileName: 'legacy.pdf', text: 'old data' }]);

        // useAuth sets "local" without claiming when /me is unreachable.
        await setLibraryOwner('local');
        expect(await getRecentBooks()).toHaveLength(0);

        await claim('userA');
        expect((await getRecentBooks()).map((b) => b.fileName)).toEqual(['legacy.pdf']);
    });

    it('never overwrites a record the claiming user already owns under that name', async () => {
        await seedLegacyV4Db([{ fileName: 'x.pdf', text: 'LEGACY' }]);

        await setLibraryOwner('userA');
        await saveBook(makeFile('x.pdf', 'FIRST'), {});

        await claim('userA');
        const book = await getBook('x.pdf');
        expect(new TextDecoder().decode(book.data)).toBe('FIRST');
    });

    it('is race-safe: two overlapping claims for different owners never double-claim', async () => {
        await seedLegacyV4Db([{ fileName: 'race.pdf', text: 'shared legacy' }]);

        // Fire both without awaiting the first — they race for the same
        // unclaimed record.
        await Promise.all([claim('userA'), claim('userB')]);

        // Exactly one owner ends up with the record, never both, never neither.
        await setLibraryOwner('userA');
        const asA = (await getRecentBooks()).length;
        await setLibraryOwner('userB');
        const asB = (await getRecentBooks()).length;
        expect(asA + asB).toBe(1);
    });
});

describe('library ownership — no owner known (before /me, after a 401, logged out)', () => {
    it('saves are refused, so nothing lands where the next user could claim it', async () => {
        await setLibraryOwner(null);
        expect(await saveBook(makeFile('orphan.pdf', 'A secret'), {})).toBe(false);
        expect(await getRecentBooks()).toHaveLength(0);

        await claim('userB');
        expect(await getRecentBooks()).toHaveLength(0);
        expect(await getBook('orphan.pdf')).toBeNull();
    });

    it('updates and deletes are no-ops', async () => {
        await setLibraryOwner('userA');
        await saveBook(makeFile('mine.pdf', 'data'), {});

        await setLibraryOwner(null);
        expect(await updateBookMeta('mine.pdf', { page: 3 })).toBe(false);
        expect(await deleteBook('mine.pdf')).toBe(false);

        await setLibraryOwner('userA');
        expect(await getRecentBooks()).toHaveLength(1);
    });
});

describe('library ownership — logout hides records without deleting them', () => {
    it('records stop showing after setLibraryOwner(null) and reappear on the same sign-in', async () => {
        await setLibraryOwner('userA');
        await saveBook(makeFile('mine.pdf', 'data'), {});
        expect(await getRecentBooks()).toHaveLength(1);

        await setLibraryOwner(null);
        expect(await getRecentBooks()).toHaveLength(0);
        expect(await getBook('mine.pdf')).toBeNull();

        await setLibraryOwner('userA');
        expect(await getRecentBooks()).toHaveLength(1);
        expect(await getBook('mine.pdf')).not.toBeNull();
    });
});

describe('library ownership — the "local" owner (/v1/auth/me unavailable)', () => {
    it('"local" behaves like any other owner id', async () => {
        await setLibraryOwner('local');
        await saveBook(makeFile('offline.pdf', 'x'), {});
        expect(await getRecentBooks()).toHaveLength(1);

        await setLibraryOwner('someone-else');
        expect(await getRecentBooks()).toHaveLength(0);
    });
});

describe('library ownership — metadata cannot move a record to another key', () => {
    it('ownerId/fileName in the metadata are ignored', async () => {
        await setLibraryOwner('userA');
        await saveBook(makeFile('a.pdf', 'x'), { ownerId: 'userB', fileName: 'b.pdf', page: 2 });
        await updateBookMeta('a.pdf', { ownerId: 'userB', fileName: 'b.pdf' });

        const book = await getBook('a.pdf');
        expect(book.ownerId).toBe('userA');
        expect(book.fileName).toBe('a.pdf');
        expect(book.page).toBe(2);
        await setLibraryOwner('userB');
        expect(await getRecentBooks()).toHaveLength(0);
    });
});

// A v1 install: keyed by fileName, no fileType (all PDFs), no other stores.
const seedLegacyV1Db = () => new Promise((resolve, reject) => {
    const req = indexedDB.open('neural-pdf-library', 1);
    req.onupgradeneeded = (event) => {
        const store = event.target.result.createObjectStore('books', { keyPath: 'fileName' });
        store.createIndex('lastOpened', 'lastOpened', { unique: false });
        store.put({ fileName: 'v1.pdf', data: new TextEncoder().encode('v1 bytes').buffer, size: 8, lastOpened: 1 });
    };
    req.onsuccess = () => { req.result.close(); resolve(); };
    req.onerror = () => reject(req.error);
});

describe('library schema migration — legacy fileName-keyed database', () => {
    it('migrates a pre-release v4 record without losing it, and it is claimable', async () => {
        await seedLegacyV4Db();

        await claim('userA');
        expect((await getRecentBooks()).map((b) => b.fileName)).toContain('old.pdf');

        const book = await getBook('old.pdf');
        expect(new TextDecoder().decode(book.data)).toBe('ancient bytes');
    });

    it('migrates a v1 install (no fileType) in one upgrade, backfilling fileType', async () => {
        await seedLegacyV1Db();

        await claim('userA');
        const books = await getRecentBooks();
        expect(books).toEqual([expect.objectContaining({ fileName: 'v1.pdf', fileType: 'pdf' })]);
        expect((await getBook('v1.pdf')).fileType).toBe('pdf');
    });
});
