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

describe('library ownership — legacy (no-owner) records are claimed once', () => {
    it('a record saved before any owner was known is claimed by the first signed-in user', async () => {
        // Simulate a pre-release record: saved while the owner is unknown.
        await setLibraryOwner(null);
        await saveBook(makeFile('legacy.pdf', 'old data'), {});

        // First user to sign in on this browser claims it.
        await setLibraryOwner('userA');
        const booksAsA = await getRecentBooks();
        expect(booksAsA.map((b) => b.fileName)).toContain('legacy.pdf');

        // A second, later user must not see it — it's already claimed.
        await setLibraryOwner('userB');
        const booksAsB = await getRecentBooks();
        expect(booksAsB.map((b) => b.fileName)).not.toContain('legacy.pdf');

        // Switching back to userA still shows it (claim is permanent, calling
        // setLibraryOwner('userA') again does not error or re-claim/duplicate).
        await setLibraryOwner('userA');
        const booksAsAAgain = await getRecentBooks();
        expect(booksAsAAgain).toHaveLength(1);
    });

    it('never overwrites an existing record the claiming user already owns under that name', async () => {
        await setLibraryOwner('userA');
        await saveBook(makeFile('x.pdf', 'FIRST'), {});

        // Logout, then a legacy-style save lands under the same name while no
        // owner is known.
        await setLibraryOwner(null);
        await saveBook(makeFile('x.pdf', 'LEGACY-AGAIN'), {});

        // userA signs back in — the claim must not clobber their own existing
        // 'x.pdf'.
        await setLibraryOwner('userA');
        const book = await getBook('x.pdf');
        expect(new TextDecoder().decode(book.data)).toBe('FIRST');
    });

    it('is race-safe: two overlapping claims for different owners never double-claim', async () => {
        await setLibraryOwner(null);
        await saveBook(makeFile('race.pdf', 'shared legacy'), {});

        // Fire both without awaiting the first — they race for the same
        // unclaimed record.
        const claimA = setLibraryOwner('userA');
        const claimB = setLibraryOwner('userB');
        await Promise.all([claimA, claimB]);

        // Whichever owner setLibraryOwner left `currentOwnerId` as is the one
        // we can directly check; the important invariant is exactly one
        // owner ends up with the record, never both, never neither.
        await setLibraryOwner('userA');
        const asA = (await getRecentBooks()).length;
        await setLibraryOwner('userB');
        const asB = (await getRecentBooks()).length;
        expect(asA + asB).toBe(1);
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

describe('library ownership — dev loopback bypass ("local")', () => {
    it('"local" behaves like any other owner id', async () => {
        await setLibraryOwner('local');
        await saveBook(makeFile('offline.pdf', 'x'), {});
        expect(await getRecentBooks()).toHaveLength(1);

        await setLibraryOwner('someone-else');
        expect(await getRecentBooks()).toHaveLength(0);
    });
});

describe('library schema migration — legacy fileName-keyed database', () => {
    // Simulates a real existing v4 install: books keyed in-line by `fileName`,
    // no `ownerId` field at all. Opens the DB at a version *older* than the
    // current DB_VERSION so db.js's own next open triggers onupgradeneeded.
    const seedLegacyV4Db = () => new Promise((resolve, reject) => {
        const req = indexedDB.open('neural-pdf-library', 4);
        req.onupgradeneeded = (event) => {
            const db = event.target.result;
            const store = db.createObjectStore('books', { keyPath: 'fileName' });
            store.createIndex('lastOpened', 'lastOpened', { unique: false });
            store.put({
                fileName: 'old.pdf',
                data: new TextEncoder().encode('ancient bytes').buffer,
                size: 13,
                fileType: 'pdf',
                lastOpened: 123,
            });
        };
        req.onsuccess = () => { req.result.close(); resolve(); };
        req.onerror = () => reject(req.error);
    });

    it('migrates a pre-release fileName-keyed record without losing it, and it is claimable', async () => {
        await resetDb();
        await seedLegacyV4Db();

        await setLibraryOwner('userA');
        const books = await getRecentBooks();
        expect(books.map((b) => b.fileName)).toContain('old.pdf');

        const book = await getBook('old.pdf');
        expect(new TextDecoder().decode(book.data)).toBe('ancient bytes');
    });
});
