/**
 * IndexedDB utility for storing and retrieving PDF files
 * Allows true "resume reading" without re-uploading files
 */

const DB_NAME = 'neural-pdf-library';
const DB_VERSION = 5;
const STORE_NAME = 'books';
const SESSIONS_STORE = 'chat_sessions';
const WORKSPACE_STORE = 'workspaces';
const MAX_BOOKS = 5; // Keep last 5 books (per owner — see cleanupOldBooks)
const MAX_SESSIONS = 50; // Cap chat history at 50 sessions (LRU by updatedAt)

// --- LIBRARY OWNERSHIP -------------------------------------------------
// The `books` store is shared by everyone who uses this browser, so every
// record carries an `ownerId` and reads/writes are scoped to whoever is
// currently signed in. `currentOwnerId` is set by the auth layer (useAuth)
// via `setLibraryOwner` once `/v1/auth/me` resolves; it starts out `null`
// ("owner not known yet"). While it is null every read finds nothing and
// every write is refused — fail-closed, so a file opened before sign-in or
// after a 401 can never land somewhere the next user could see or claim.
let currentOwnerId = null;

// Sentinel `ownerId` for records saved before this release (no `ownerId`
// field at all). Only the v4 → v5 migration writes it, and only a claim
// (setLibraryOwner with claimLegacy) reads it. It's a real string (never
// `null`/`undefined`) so it can live in an IndexedDB index — indexes
// silently skip records whose indexed property is null/undefined.
const UNCLAIMED_OWNER = '__unclaimed__';

// The store keeps out-of-line (explicit) keys built from owner + fileName so
// two different users can each save a file with the same name without
// colliding — see the v4 → v5 migration in openDB() for why this replaced
// the old `keyPath: 'fileName'` scheme. `\u0000` can't appear in a fileName
// a browser would give us, so it's a safe separator.
const bookKey = (ownerId, fileName) => `${ownerId}\u0000${fileName}`;

/**
 * Set the signed-in user whose books/library reads and writes should be
 * scoped to. Called by the auth layer once `/v1/auth/me` resolves — with the
 * real user id when signed in, or `null` when no user is known (logout, a
 * lost session, `/v1/auth/me` unavailable), which simply hides every record
 * until an owner is set again — nothing is deleted.
 *
 * With `claimLegacy`, also claims every not-yet-owned (pre-release) record
 * for this owner, so a single-user install keeps its existing library. Only
 * an id `/v1/auth/me` actually returned passes it.
 * The claim is one read-write transaction (getAll the
 * UNCLAIMED_OWNER index, then delete+put each record under its new key), so
 * it's atomic: if two owners are set around the same time (e.g. two tabs),
 * IndexedDB serializes their read-write transactions against this store —
 * whichever commits first claims everything currently unclaimed, and the
 * second one's read then finds nothing left to claim. It's also idempotent:
 * once nothing is left in the UNCLAIMED_OWNER bucket, calling this again for
 * the same (or any) owner is a cheap no-op.
 */
export const setLibraryOwner = async (id, { claimLegacy = false } = {}) => {
    const newOwner = id || null;
    currentOwnerId = newOwner;
    if (!newOwner || !claimLegacy) return;

    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        const index = store.index('ownerId');

        const unclaimed = await new Promise((resolve, reject) => {
            const request = index.getAll(IDBKeyRange.only(UNCLAIMED_OWNER));
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });

        for (const rec of unclaimed) {
            const newKey = bookKey(newOwner, rec.fileName);

            // Don't clobber a record the new owner already has under that
            // name — leave the legacy one unclaimed rather than lose either.
            const existing = await new Promise((resolve, reject) => {
                const request = store.get(newKey);
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
            if (existing) continue;

            await new Promise((resolve, reject) => {
                const request = store.delete(bookKey(UNCLAIMED_OWNER, rec.fileName));
                request.onsuccess = resolve;
                request.onerror = () => reject(request.error);
            });
            await new Promise((resolve, reject) => {
                const request = store.put({ ...rec, ownerId: newOwner }, newKey);
                request.onsuccess = resolve;
                request.onerror = () => reject(request.error);
            });
        }

        db.close();
    } catch (e) {
        console.error('Failed to claim legacy library records:', e);
    }
};

// Derive fileType from a File object's name/MIME type
export const detectFileType = (file) => {
    if (!file) return 'pdf';
    const name = (file.name || '').toLowerCase();
    if (file.type === 'text/markdown' || name.endsWith('.md') || name.endsWith('.markdown')) return 'markdown';
    if (file.type === 'text/plain' || name.endsWith('.txt')) return 'text';
    return 'pdf';
};

// Open database connection
const openDB = () => {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);

        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve(request.result);

        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            const tx = event.target.transaction;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                // Fresh install: create directly in the current (v5) shape —
                // out-of-line keys (see bookKey) plus an `ownerId` index, so
                // there's nothing to migrate later.
                const store = db.createObjectStore(STORE_NAME);
                store.createIndex('lastOpened', 'lastOpened', { unique: false });
                store.createIndex('ownerId', 'ownerId', { unique: false });
            } else if (event.oldVersion < 5) {
                // v4 (or earlier) → v5: the store used to be keyed in-line by
                // `fileName` with no `ownerId` field at all, which meant two
                // users saving a same-named file would silently overwrite
                // each other. Read every record out, rebuild the store with
                // out-of-line composite keys (owner + fileName), and write
                // them all back tagged UNCLAIMED_OWNER so the first user to
                // sign in claims them (see setLibraryOwner). Nothing is
                // dropped. This also does the old v1 → v2 step (backfill
                // fileType; every v1 record was a PDF): any version below 5
                // passes through here, and the old store is gone afterwards.
                const oldStore = tx.objectStore(STORE_NAME);
                const getAllReq = oldStore.getAll();
                getAllReq.onsuccess = () => {
                    const legacyRecords = getAllReq.result || [];
                    db.deleteObjectStore(STORE_NAME);
                    const newStore = db.createObjectStore(STORE_NAME);
                    newStore.createIndex('lastOpened', 'lastOpened', { unique: false });
                    newStore.createIndex('ownerId', 'ownerId', { unique: false });
                    for (const rec of legacyRecords) {
                        const ownerId = rec.ownerId || UNCLAIMED_OWNER;
                        const fileType = rec.fileType || 'pdf';
                        newStore.put({ ...rec, fileType, ownerId }, bookKey(ownerId, rec.fileName));
                    }
                };
            }
            // v2 → v3: add chat sessions store
            if (event.oldVersion < 3) {
                if (!db.objectStoreNames.contains(SESSIONS_STORE)) {
                    const sessionsStore = db.createObjectStore(SESSIONS_STORE, { keyPath: 'id' });
                    sessionsStore.createIndex('updatedAt', 'updatedAt', { unique: false });
                }
            }
            // v3 → v4: workspace persistence (one record, id 'last')
            if (event.oldVersion < 4) {
                if (!db.objectStoreNames.contains(WORKSPACE_STORE)) {
                    db.createObjectStore(WORKSPACE_STORE, { keyPath: 'id' });
                }
            }
        };
    });
};

/**
 * Save a PDF to IndexedDB
 * @param {File} file - The PDF file object
 * @param {Object} metadata - Additional metadata (page, sentenceIndex, etc.)
 */
export const saveBook = async (file, metadata = {}) => {
    try {
        const ownerId = currentOwnerId;
        if (!ownerId) return false; // no signed-in user known — see currentOwnerId
        const db = await openDB();
        const arrayBuffer = await file.arrayBuffer();

        // fileName and ownerId come last: they make up the key, so metadata
        // can never leave a record under a key that doesn't match it.
        const bookData = {
            data: arrayBuffer,
            size: file.size,
            fileType: detectFileType(file),
            lastOpened: Date.now(),
            ...metadata,
            fileName: file.name,
            ownerId,
        };

        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        await new Promise((resolve, reject) => {
            const request = store.put(bookData, bookKey(ownerId, file.name));
            request.onsuccess = resolve;
            request.onerror = () => reject(request.error);
        });

        // Clean up old books within the same transaction (this owner's only)
        await cleanupOldBooks(store, ownerId);

        db.close();
        return true;
    } catch (e) {
        console.error('Failed to save book:', e);
        return false;
    }
};

/**
 * Get a PDF from IndexedDB by filename
 * @param {string} fileName - The filename to retrieve
 * @returns {Object|null} - Book data with ArrayBuffer or null
 */
export const getBook = async (fileName) => {
    if (!currentOwnerId) return null;
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);

        // Scoped to the current owner's key — a record saved by someone else
        // under the same fileName lives at a different key entirely, so this
        // simply won't find it and returns null, same as if it never existed.
        const result = await new Promise((resolve, reject) => {
            const request = store.get(bookKey(currentOwnerId, fileName));
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });

        db.close();
        return result || null;
    } catch (e) {
        console.error('Failed to get book:', e);
        return null;
    }
};

/**
 * Get list of all stored books (metadata only, no data)
 * @returns {Array} - List of book metadata sorted by lastOpened (newest first)
 */
export const getRecentBooks = async () => {
    if (!currentOwnerId) return [];
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const index = store.index('ownerId');

        const books = await new Promise((resolve, reject) => {
            const request = index.getAll(IDBKeyRange.only(currentOwnerId));
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });

        db.close();

        // Return sorted by lastOpened (newest first), without the data blob
        return books
            .map((book) => ({
                fileName: book.fileName,
                size: book.size,
                lastOpened: book.lastOpened,
                fileType: book.fileType || 'pdf',
            }))
            .sort((a, b) => b.lastOpened - a.lastOpened);
    } catch (e) {
        console.error('Failed to get recent books:', e);
        return [];
    }
};

/**
 * Delete a book from IndexedDB
 * @param {string} fileName - The filename to delete
 */
export const deleteBook = async (fileName) => {
    if (!currentOwnerId) return false;
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        // Scoped to the current owner's key, so this can only ever delete
        // the caller's own record, never one another user saved under the
        // same fileName.
        await new Promise((resolve, reject) => {
            const request = store.delete(bookKey(currentOwnerId, fileName));
            request.onsuccess = resolve;
            request.onerror = () => reject(request.error);
        });

        db.close();
        return true;
    } catch (e) {
        console.error('Failed to delete book:', e);
        return false;
    }
};

/**
 * Update book metadata (e.g., reading progress)
 * Uses a single transaction to avoid race conditions.
 * @param {string} fileName - The filename to update
 * @param {Object} updates - Fields to update
 */
export const updateBookMeta = async (fileName, updates) => {
    if (!currentOwnerId) return false;
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        const key = bookKey(currentOwnerId, fileName);

        // Read and write in the same transaction to avoid race conditions.
        // Scoped to the current owner's key, so a name the caller doesn't
        // own (even if someone else has one under that name) is a no-op.
        const book = await new Promise((resolve, reject) => {
            const request = store.get(key);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });

        if (!book) {
            db.close();
            return false;
        }

        const updatedBook = {
            ...book,
            ...updates,
            lastOpened: Date.now(),
            fileName: book.fileName, // part of the key — see saveBook
            ownerId: book.ownerId,
        };

        await new Promise((resolve, reject) => {
            const request = store.put(updatedBook, key);
            request.onsuccess = resolve;
            request.onerror = () => reject(request.error);
        });

        db.close();
        return true;
    } catch (e) {
        console.error('Failed to update book:', e);
        return false;
    }
};

/**
 * Internal: Remove oldest books if over limit, scoped to one owner — so one
 * user's uploads never evict another user's saved books.
 * Accepts an existing store to reuse the caller's transaction.
 */
const cleanupOldBooks = async (store, ownerId) => {
    const index = store.index('ownerId');

    const books = await new Promise((resolve, reject) => {
        const request = index.getAll(IDBKeyRange.only(ownerId));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });

    if (books.length > MAX_BOOKS) {
        // Sort by lastOpened ascending (oldest first)
        books.sort((a, b) => a.lastOpened - b.lastOpened);

        // Delete oldest books
        const toDelete = books.slice(0, books.length - MAX_BOOKS);
        for (const book of toDelete) {
            await new Promise((resolve) => {
                const request = store.delete(bookKey(book.ownerId, book.fileName));
                request.onsuccess = resolve;
                request.onerror = resolve; // Continue even on error
            });
        }
    }
};

// =====================================================================
// CHAT SESSIONS
// Each session record:
//   { id, title, model, createdAt, updatedAt, messages: [...], events: [...] }
// `messages` is the full chat history; `events` is a per-session log
// (sent / received / aborted / error). The recents listing strips both
// for cheap rendering.
// =====================================================================

export const saveSession = async (session) => {
    if (!session?.id) return false;
    try {
        const db = await openDB();
        const record = { ...session, updatedAt: Date.now() };
        const tx = db.transaction(SESSIONS_STORE, 'readwrite');
        const store = tx.objectStore(SESSIONS_STORE);
        await new Promise((resolve, reject) => {
            const req = store.put(record);
            req.onsuccess = resolve;
            req.onerror = () => reject(req.error);
        });
        await cleanupOldSessions(store);
        db.close();
        return true;
    } catch (e) {
        console.error('Failed to save chat session:', e);
        return false;
    }
};

export const getSession = async (id) => {
    if (!id) return null;
    try {
        const db = await openDB();
        const tx = db.transaction(SESSIONS_STORE, 'readonly');
        const store = tx.objectStore(SESSIONS_STORE);
        const result = await new Promise((resolve, reject) => {
            const req = store.get(id);
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
        db.close();
        return result || null;
    } catch (e) {
        console.error('Failed to get chat session:', e);
        return null;
    }
};

// List all sessions (metadata only — no messages or events) sorted newest-first.
export const getRecentSessions = async () => {
    try {
        const db = await openDB();
        const tx = db.transaction(SESSIONS_STORE, 'readonly');
        const store = tx.objectStore(SESSIONS_STORE);
        const records = await new Promise((resolve, reject) => {
            const req = store.getAll();
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
        db.close();
        return records
            .map((s) => ({
                id: s.id,
                title: s.title,
                model: s.model,
                createdAt: s.createdAt,
                updatedAt: s.updatedAt,
                messageCount: Array.isArray(s.messages) ? s.messages.length : 0,
            }))
            .sort((a, b) => b.updatedAt - a.updatedAt);
    } catch (e) {
        console.error('Failed to list chat sessions:', e);
        return [];
    }
};

export const deleteSession = async (id) => {
    if (!id) return false;
    try {
        const db = await openDB();
        const tx = db.transaction(SESSIONS_STORE, 'readwrite');
        const store = tx.objectStore(SESSIONS_STORE);
        await new Promise((resolve, reject) => {
            const req = store.delete(id);
            req.onsuccess = resolve;
            req.onerror = () => reject(req.error);
        });
        db.close();
        return true;
    } catch (e) {
        console.error('Failed to delete chat session:', e);
        return false;
    }
};

const cleanupOldSessions = async (store) => {
    const index = store.index('updatedAt');
    const sessions = await new Promise((resolve, reject) => {
        const req = index.getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
    if (sessions.length > MAX_SESSIONS) {
        sessions.sort((a, b) => a.updatedAt - b.updatedAt);
        const toDelete = sessions.slice(0, sessions.length - MAX_SESSIONS);
        for (const s of toDelete) {
            await new Promise((resolve) => {
                const req = store.delete(s.id);
                req.onsuccess = resolve;
                req.onerror = resolve;
            });
        }
    }
};

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
