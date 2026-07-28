/**
 * IndexedDB utility for storing and retrieving PDF files
 * Allows true "resume reading" without re-uploading files
 */

const DB_NAME = 'neural-pdf-library';
const DB_VERSION = 4;
const STORE_NAME = 'books';
const SESSIONS_STORE = 'chat_sessions';
const WORKSPACE_STORE = 'workspaces';
const MAX_BOOKS = 5; // Keep last 5 books
const MAX_SESSIONS = 50; // Cap chat history at 50 sessions (LRU by updatedAt)

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
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                const store = db.createObjectStore(STORE_NAME, { keyPath: 'fileName' });
                store.createIndex('lastOpened', 'lastOpened', { unique: false });
            }
            // v1 → v2: backfill fileType on existing records (all were PDFs)
            if (event.oldVersion < 2) {
                const tx = event.target.transaction;
                const store = tx.objectStore(STORE_NAME);
                const cursorReq = store.openCursor();
                cursorReq.onsuccess = (e) => {
                    const cursor = e.target.result;
                    if (!cursor) return;
                    const value = cursor.value;
                    if (!value.fileType) {
                        cursor.update({ ...value, fileType: 'pdf' });
                    }
                    cursor.continue();
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
        const db = await openDB();
        const arrayBuffer = await file.arrayBuffer();

        const bookData = {
            fileName: file.name,
            data: arrayBuffer,
            size: file.size,
            fileType: detectFileType(file),
            lastOpened: Date.now(),
            ...metadata
        };

        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        await new Promise((resolve, reject) => {
            const request = store.put(bookData);
            request.onsuccess = resolve;
            request.onerror = () => reject(request.error);
        });

        // Clean up old books within the same transaction
        await cleanupOldBooks(store);

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
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);

        const result = await new Promise((resolve, reject) => {
            const request = store.get(fileName);
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
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);

        const books = await new Promise((resolve, reject) => {
            const request = store.getAll();
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
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        await new Promise((resolve, reject) => {
            const request = store.delete(fileName);
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
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        // Read and write in the same transaction to avoid race conditions
        const book = await new Promise((resolve, reject) => {
            const request = store.get(fileName);
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
            lastOpened: Date.now()
        };

        await new Promise((resolve, reject) => {
            const request = store.put(updatedBook);
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
 * Internal: Remove oldest books if over limit.
 * Accepts an existing store to reuse the caller's transaction.
 */
const cleanupOldBooks = async (store) => {
    const index = store.index('lastOpened');

    const books = await new Promise((resolve, reject) => {
        const request = index.getAll();
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
                const request = store.delete(book.fileName);
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
