/**
 * IndexedDB utility for storing and retrieving PDF files
 * Allows true "resume reading" without re-uploading files
 */

const DB_NAME = 'neural-pdf-library';
const DB_VERSION = 1;
const STORE_NAME = 'books';
const MAX_BOOKS = 5; // Keep last 5 books

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

        // Clean up old books if we have too many
        await cleanupOldBooks(db);

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
            .map(({ data, ...meta }) => meta)
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
 * @param {string} fileName - The filename to update
 * @param {Object} updates - Fields to update
 */
export const updateBookMeta = async (fileName, updates) => {
    try {
        const book = await getBook(fileName);
        if (!book) return false;

        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);

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

// Internal: Remove oldest books if over limit
const cleanupOldBooks = async (db) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
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
