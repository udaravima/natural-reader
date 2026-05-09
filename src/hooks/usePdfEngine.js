import { useState, useEffect, useRef } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import { saveBook, getBook, getRecentBooks, deleteBook, updateBookMeta, detectFileType } from '../db';
import { loadReadingProgress, saveReadingProgress } from './usePersistedState';
import { SENTENCES_PER_TEXT_PAGE } from '../constants';

// Configure PDF.js worker for offline use
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
    'pdfjs-dist/build/pdf.worker.min.mjs',
    import.meta.url
).toString();

// Shared sentence segmentation: same rule for PDF and .txt so the TTS engine
// receives a consistent textItems shape regardless of source.
const segmentSentences = (rawText) =>
    rawText
        .replace(/\s+/g, ' ')
        .split(/(?<=[.!?])\s+/)
        .filter(s => s.trim().length > 5);

// Chunk a flat sentence array into pseudo-pages of N sentences.
const paginateSentences = (sentences, perPage = SENTENCES_PER_TEXT_PAGE) => {
    const pages = [];
    for (let i = 0; i < sentences.length; i += perPage) {
        pages.push(sentences.slice(i, i + perPage));
    }
    return pages.length > 0 ? pages : [[]];
};

/**
 * Manages PDF document loading, rendering, text extraction, outline, and library.
 */
export function usePdfEngine({ scale, setStatus, setToastMessage }) {
    const [pdfDoc, setPdfDoc] = useState(null);
    const [pdfFileName, setPdfFileName] = useState('');
    const [fileType, setFileType] = useState('pdf'); // 'pdf' | 'text'
    const [textPages, setTextPages] = useState([]); // sentences[][] for .txt files
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

    // Clear text items immediately when page changes to prevent stale TTS playback.
    // Without this, the TTS loop could re-run with the old page's text before
    // renderPage finishes extracting the new page's text.
    const prevPageRef = useRef(currentPage);
    useEffect(() => {
        if (prevPageRef.current !== currentPage) {
            prevPageRef.current = currentPage;
            setTextItems([]);
        }
    }, [currentPage]);

    // --- PDF RENDERING ---
    // Visual render: canvas + text selection layer (runs on page, scale, or doc change)
    const renderPageVisual = async (pageNum, doc) => {
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

            setStatus(`Page ${pageNum} Ready`);
        } catch (err) {
            console.error(err);
            setStatus("Render Error");
        }
    };

    // Text extraction: only runs when the page or document changes (NOT on scale change)
    // This prevents zoom from destroying the TTS audio cache and interrupting playback.
    const extractPageText = async (pageNum, doc) => {
        if (!doc || !pdfjsLibRef.current) return;
        try {
            const page = await doc.getPage(pageNum);
            const textContent = await page.getTextContent();
            const rawText = textContent.items.map(item => item.str).join(' ');
            const sentences = segmentSentences(rawText);
            setTextItems(sentences);
            sentenceRefs.current = sentences.map(() => null);
        } catch (err) {
            console.error('Text extraction error:', err);
        }
    };

    // Visual render on any of: doc, page, or scale change (PDF only — txt has no canvas)
    useEffect(() => {
        if (fileType === 'pdf' && pdfDoc) renderPageVisual(currentPage, pdfDoc);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pdfDoc, currentPage, scale, fileType]);

    // Sentence source per file type. For PDF we re-extract on page change; for text
    // we slice the pre-paginated array.
    useEffect(() => {
        if (fileType === 'pdf') {
            if (pdfDoc) extractPageText(currentPage, pdfDoc);
            return;
        }
        if (fileType === 'text' && textPages.length > 0) {
            const sentences = textPages[currentPage - 1] || [];
            setTextItems(sentences);
            sentenceRefs.current = sentences.map(() => null);
            setStatus(`Page ${currentPage} Ready`);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pdfDoc, currentPage, fileType, textPages]);

    // --- FILE PROCESSING ---
    // Apply saved reading progress (or reset to page 1) for the just-loaded document.
    const applySavedProgress = (fileName, totalPages) => {
        const savedProgress = loadReadingProgress(fileName);
        if (savedProgress && savedProgress.page <= totalPages) {
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
    };

    // Load a plain-text document from a UTF-8 string: paginate, set state,
    // and clear PDF-only state so the viewer renders the text path.
    const loadTextDocument = (rawText, fileName) => {
        const sentences = segmentSentences(rawText);
        const pages = paginateSentences(sentences, SENTENCES_PER_TEXT_PAGE);
        setPdfDoc(null);
        setPdfOutline([]);
        setTextPages(pages);
        setNumPages(pages.length);
        setFileType('text');
        setPdfFileName(fileName);
        applySavedProgress(fileName, pages.length);
    };

    const processFile = (file) => {
        if (!file || !isLibLoaded) return;
        const detected = detectFileType(file);
        const fileName = file.name;

        if (detected === 'pdf') {
            const reader = new FileReader();
            reader.onload = async (ev) => {
                try {
                    const loadingTask = pdfjsLibRef.current.getDocument({ data: ev.target.result });
                    const doc = await loadingTask.promise;
                    setFileType('pdf');
                    setTextPages([]);
                    setPdfDoc(doc);
                    setNumPages(doc.numPages);
                    setPdfFileName(fileName);

                    applySavedProgress(fileName, doc.numPages);

                    // Save to IndexedDB for library persistence
                    saveBook(file, { page: 1, sentenceIndex: -1 }).then(() => {
                        getRecentBooks().then(setRecentBooks);
                    });

                    // Fetch PDF outline (Table of Contents)
                    try {
                        const outline = await doc.getOutline();
                        setPdfOutline(outline && outline.length > 0 ? outline : []);
                    } catch (e) {
                        console.warn('Could not load outline:', e);
                        setPdfOutline([]);
                    }
                } catch {
                    setStatus("Error loading PDF");
                }
            };
            reader.readAsArrayBuffer(file);
            return;
        }

        if (detected === 'text') {
            const reader = new FileReader();
            reader.onload = (ev) => {
                try {
                    const rawText = ev.target.result;
                    loadTextDocument(rawText, fileName);
                    saveBook(file, { page: 1, sentenceIndex: -1 }).then(() => {
                        getRecentBooks().then(setRecentBooks);
                    });
                } catch (e) {
                    console.error('Failed to load text file:', e);
                    setStatus("Error loading text file");
                }
            };
            reader.readAsText(file);
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

            const storedType = bookData.fileType || 'pdf';

            if (storedType === 'text') {
                const rawText = new TextDecoder().decode(bookData.data);
                loadTextDocument(rawText, fileName);
                updateBookMeta(fileName, {}).then(() => {
                    getRecentBooks().then(setRecentBooks);
                });
                return;
            }

            const loadingTask = pdfjsLibRef.current.getDocument({ data: bookData.data });
            const doc = await loadingTask.promise;

            setFileType('pdf');
            setTextPages([]);
            setPdfDoc(doc);
            setNumPages(doc.numPages);
            setPdfFileName(fileName);

            applySavedProgress(fileName, doc.numPages);

            updateBookMeta(fileName, {}).then(() => {
                getRecentBooks().then(setRecentBooks);
            });

            try {
                const outline = await doc.getOutline();
                setPdfOutline(outline && outline.length > 0 ? outline : []);
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
        fileType,
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
