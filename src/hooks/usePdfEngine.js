import { useState, useEffect, useRef } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import { saveBook, getBook, getRecentBooks, deleteBook, updateBookMeta } from '../db';
import { loadReadingProgress, saveReadingProgress } from './usePersistedState';

// Configure PDF.js worker for offline use
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
    'pdfjs-dist/build/pdf.worker.min.mjs',
    import.meta.url
).toString();

/**
 * Manages PDF document loading, rendering, text extraction, outline, and library.
 */
export function usePdfEngine({ scale, setStatus, setToastMessage }) {
    const [pdfDoc, setPdfDoc] = useState(null);
    const [pdfFileName, setPdfFileName] = useState('');
    const [currentPage, setCurrentPage] = useState(1);
    const [numPages, setNumPages] = useState(0);
    const [textItems, setTextItems] = useState([]);
    const [isLibLoaded, setIsLibLoaded] = useState(false);
    const [pdfOutline, setPdfOutline] = useState([]);
    const [recentBooks, setRecentBooks] = useState([]);
    const [currentSentenceIndex, setCurrentSentenceIndex] = useState(-1);

    const canvasRef = useRef(null);
    const textLayerRef = useRef(null);
    const fileInputRef = useRef(null);
    const pdfjsLibRef = useRef(null);
    const sentenceRefs = useRef([]);
    const playbackIndexRef = useRef(-1);

    // --- ENGINE INITIALIZATION ---
    useEffect(() => {
        pdfjsLibRef.current = pdfjsLib;
        setIsLibLoaded(true);
        setStatus('Ready to Open PDF');

        // Load recent books from IndexedDB
        const loadRecentBooks = async () => {
            const books = await getRecentBooks();
            setRecentBooks(books);
        };
        loadRecentBooks();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // --- READING PROGRESS PERSISTENCE ---
    useEffect(() => {
        saveReadingProgress(pdfFileName, currentPage, currentSentenceIndex);
    }, [pdfFileName, currentPage, currentSentenceIndex]);

    // --- AUTO-SCROLL TO CURRENT SENTENCE ---
    useEffect(() => {
        if (currentSentenceIndex >= 0 && sentenceRefs.current[currentSentenceIndex]) {
            sentenceRefs.current[currentSentenceIndex].scrollIntoView({
                behavior: 'smooth',
                block: 'center',
            });
        }
    }, [currentSentenceIndex]);

    // Sync ref with state when state changes externally
    useEffect(() => {
        playbackIndexRef.current = currentSentenceIndex;
    }, [currentSentenceIndex]);

    // --- PDF RENDERING ---
    const renderPage = async (pageNum, doc) => {
        if (!doc || !pdfjsLibRef.current) return;
        try {
            setStatus("Rendering page...");
            const page = await doc.getPage(pageNum);
            const viewport = page.getViewport({ scale });
            const canvas = canvasRef.current;
            const context = canvas.getContext('2d');
            canvas.height = viewport.height;
            canvas.width = viewport.width;

            await page.render({ canvasContext: context, viewport }).promise;

            // Render text layer for text selection
            const textContent = await page.getTextContent();
            const textLayerDiv = textLayerRef.current;
            if (textLayerDiv) {
                textLayerDiv.innerHTML = '';
                textLayerDiv.style.width = `${viewport.width}px`;
                textLayerDiv.style.height = `${viewport.height}px`;

                textContent.items.forEach((item) => {
                    if (!item.str) return;

                    const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
                    const fontHeight = Math.sqrt((tx[2] * tx[2]) + (tx[3] * tx[3]));
                    const angle = Math.atan2(tx[1], tx[0]);

                    const span = document.createElement('span');
                    span.textContent = item.str;
                    span.style.left = `${tx[4]}px`;
                    span.style.top = `${tx[5] - fontHeight}px`;
                    span.style.fontSize = `${fontHeight}px`;
                    span.style.fontFamily = item.fontName || 'sans-serif';

                    if (angle !== 0) {
                        span.style.transform = `rotate(${angle}rad)`;
                    }

                    textLayerDiv.appendChild(span);
                });
            }

            const rawText = textContent.items.map(item => item.str).join(' ');

            // Clean up text and split into manageable sentences
            const sentences = rawText
                .replace(/\s+/g, ' ')
                .split(/(?<=[.!?])\s+/)
                .filter(s => s.trim().length > 5);

            setTextItems(sentences);
            sentenceRefs.current = sentences.map(() => null);
            setStatus(`Page ${pageNum} Ready`);
            return true; // signal cache should be cleared
        } catch (err) {
            console.error(err);
            setStatus("Render Error");
            return false;
        }
    };

    useEffect(() => {
        if (pdfDoc) renderPage(currentPage, pdfDoc);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pdfDoc, currentPage, scale]);

    // --- FILE PROCESSING ---
    const processFile = (file) => {
        if (file?.type === 'application/pdf' && isLibLoaded) {
            const fileName = file.name;

            const reader = new FileReader();
            reader.onload = async (ev) => {
                try {
                    const loadingTask = pdfjsLibRef.current.getDocument({ data: ev.target.result });
                    const doc = await loadingTask.promise;
                    setPdfDoc(doc);
                    setNumPages(doc.numPages);
                    setPdfFileName(fileName);

                    // Check for saved reading progress
                    const savedProgress = loadReadingProgress(fileName);
                    if (savedProgress && savedProgress.page <= doc.numPages) {
                        setCurrentPage(savedProgress.page);
                        setTimeout(() => {
                            if (savedProgress.sentenceIndex >= 0) {
                                setCurrentSentenceIndex(savedProgress.sentenceIndex);
                                playbackIndexRef.current = savedProgress.sentenceIndex;
                            }
                        }, 500);
                        setStatus(`Resumed from page ${savedProgress.page}`);
                    } else {
                        setCurrentPage(1);
                        setCurrentSentenceIndex(-1);
                        playbackIndexRef.current = -1;
                    }

                    // Save to IndexedDB for library persistence
                    saveBook(file, { page: 1, sentenceIndex: -1 }).then(() => {
                        getRecentBooks().then(setRecentBooks);
                    });

                    // Fetch PDF outline (Table of Contents)
                    try {
                        const outline = await doc.getOutline();
                        if (outline && outline.length > 0) {
                            setPdfOutline(outline);
                        } else {
                            setPdfOutline([]);
                        }
                    } catch (e) {
                        console.warn('Could not load outline:', e);
                        setPdfOutline([]);
                    }
                } catch {
                    setStatus("Error loading PDF");
                }
            };
            reader.readAsArrayBuffer(file);
        }
    };

    // --- LIBRARY OPERATIONS ---
    const openFromLibrary = async (fileName) => {
        setStatus(`Loading ${fileName}...`);
        try {
            const bookData = await getBook(fileName);
            if (!bookData) {
                setStatus("Book not found in library");
                setToastMessage("Book not found. Please re-upload.");
                setTimeout(() => setToastMessage(null), 4000);
                return;
            }

            const loadingTask = pdfjsLibRef.current.getDocument({ data: bookData.data });
            const doc = await loadingTask.promise;

            setPdfDoc(doc);
            setNumPages(doc.numPages);
            setPdfFileName(fileName);

            const savedProgress = loadReadingProgress(fileName);
            if (savedProgress && savedProgress.page <= doc.numPages) {
                setCurrentPage(savedProgress.page);
                setTimeout(() => {
                    if (savedProgress.sentenceIndex >= 0) {
                        setCurrentSentenceIndex(savedProgress.sentenceIndex);
                        playbackIndexRef.current = savedProgress.sentenceIndex;
                    }
                }, 500);
                setStatus(`Resumed "${fileName}" from page ${savedProgress.page}`);
            } else {
                setCurrentPage(1);
                setCurrentSentenceIndex(-1);
                playbackIndexRef.current = -1;
                setStatus(`Opened "${fileName}"`);
            }

            updateBookMeta(fileName, {}).then(() => {
                getRecentBooks().then(setRecentBooks);
            });

            try {
                const outline = await doc.getOutline();
                if (outline && outline.length > 0) {
                    setPdfOutline(outline);
                } else {
                    setPdfOutline([]);
                }
            } catch {
                setPdfOutline([]);
            }
        } catch (e) {
            console.error("Failed to open from library:", e);
            setStatus("Failed to load book");
        }
    };

    const removeFromLibrary = async (fileName, e) => {
        e.stopPropagation();
        await deleteBook(fileName);
        const books = await getRecentBooks();
        setRecentBooks(books);
        setToastMessage(`Removed "${fileName}" from library`);
        setTimeout(() => setToastMessage(null), 3000);
    };

    const handleFileUpload = (e) => {
        const file = e.target.files[0];
        processFile(file);
    };

    // --- READING STATISTICS ---
    const calculateReadingProgress = () => {
        if (textItems.length === 0) return 0;
        return Math.round(((currentSentenceIndex + 1) / textItems.length) * 100);
    };

    const calculateEstimatedTimeRemaining = (playbackSpeed) => {
        if (textItems.length === 0 || currentSentenceIndex < 0) return null;

        const remainingSentences = textItems.slice(currentSentenceIndex + 1);
        const remainingWords = remainingSentences.reduce((acc, s) => acc + s.split(' ').length, 0);

        const wordsPerMinute = 150 * playbackSpeed;
        const minutesRemaining = remainingWords / wordsPerMinute;

        if (minutesRemaining < 1) return 'Less than 1 min';
        if (minutesRemaining < 60) return `~${Math.ceil(minutesRemaining)} min`;
        const hours = Math.floor(minutesRemaining / 60);
        const mins = Math.ceil(minutesRemaining % 60);
        return `~${hours}h ${mins}m`;
    };

    return {
        // State
        pdfDoc,
        pdfFileName,
        currentPage, setCurrentPage,
        numPages,
        textItems,
        isLibLoaded,
        pdfOutline,
        recentBooks,
        currentSentenceIndex, setCurrentSentenceIndex,

        // Refs
        canvasRef,
        textLayerRef,
        fileInputRef,
        sentenceRefs,
        playbackIndexRef,

        // Actions
        processFile,
        openFromLibrary,
        removeFromLibrary,
        handleFileUpload,
        calculateReadingProgress,
        calculateEstimatedTimeRemaining,
    };
}
