import { useState, useEffect, useRef } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import { fromMarkdown } from 'mdast-util-from-markdown';
import { gfm } from 'micromark-extension-gfm';
import { gfmFromMarkdown } from 'mdast-util-gfm';
import { toString as mdastToString } from 'mdast-util-to-string';
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

// --- MARKDOWN ---
// AST-driven block segmentation: parse with the same micromark/mdast pipeline
// react-markdown uses (CommonMark + GFM), so each top-level mdast child maps
// 1:1 to a block-level component invocation in the renderer. This is what
// drives `paragraphMap` (sentence index → block index) — any mismatch here
// would slide the highlight to the wrong block.
//
// Block carries:
//   raw       — exact source slice (what react-markdown re-parses + renders)
//   plain     — stripped text for sentence extraction / TTS
//   sentences — TTS sentence array (empty for code blocks: skipped by TTS)
//   type      — mdast node type (paragraph, heading, list, code, blockquote, …)
const splitSentences = (text) =>
    text
        .replace(/\s+/g, ' ')
        .trim()
        .split(/(?<=[.!?])\s+/)
        .map(s => s.trim())
        .filter(s => s.length > 0);

const segmentMarkdown = (rawText) => {
    if (!rawText) return [];
    const tree = fromMarkdown(rawText, {
        extensions: [gfm()],
        mdastExtensions: [gfmFromMarkdown()],
    });
    const blocks = [];
    for (const node of tree.children) {
        // Skip nodes that don't render visibly — they'd inflate the block count
        // and slide the highlight off by one for every following block.
        if (node.type === 'definition' || node.type === 'footnoteDefinition') continue;
        const start = node.position?.start?.offset ?? 0;
        const end = node.position?.end?.offset ?? rawText.length;
        const raw = rawText.slice(start, end);
        const isCode = node.type === 'code';
        const plain = isCode ? '' : mdastToString(node);
        const sentences = isCode ? [] : splitSentences(plain);
        blocks.push({ raw, plain, sentences, type: node.type });
    }
    return blocks;
};

// Greedy pagination: pack blocks until the next would exceed the soft cap.
// Pages always end on a block boundary; a single oversize block gets its own page.
//
// `blockOffsets[i] = [start, end)` — the byte range of block `i` within the joined
// `rawMarkdown` string. The renderer uses these to map any rendered node (top-level
// OR nested) back to its enclosing block via `node.position.start.offset`.
const buildMarkdownPage = (blocks) => {
    const sentences = [];
    const paragraphMap = [];
    const blockOffsets = [];
    const parts = [];
    let cursor = 0;
    blocks.forEach((block, blockIndex) => {
        const start = cursor;
        parts.push(block.raw);
        cursor += block.raw.length;
        blockOffsets.push([start, cursor]);
        if (blockIndex < blocks.length - 1) {
            parts.push('\n\n');
            cursor += 2;
        }
        for (const s of block.sentences) {
            sentences.push(s);
            paragraphMap.push(blockIndex);
        }
    });
    return { rawMarkdown: parts.join(''), blocks, sentences, paragraphMap, blockOffsets };
};

const paginateMarkdownBlocks = (blocks, perPage = SENTENCES_PER_TEXT_PAGE) => {
    const pages = [];
    let current = [];
    let count = 0;
    for (const block of blocks) {
        const size = block.sentences.length;
        if (current.length > 0 && count + size > perPage) {
            pages.push(buildMarkdownPage(current));
            current = [];
            count = 0;
        }
        current.push(block);
        count += size;
    }
    if (current.length > 0) pages.push(buildMarkdownPage(current));
    return pages.length > 0 ? pages : [buildMarkdownPage([])];
};

/**
 * Manages PDF document loading, rendering, text extraction, outline, and library.
 */
export function usePdfEngine({ scale, setStatus, setToastMessage }) {
    const [pdfDoc, setPdfDoc] = useState(null);
    const [pdfFileName, setPdfFileName] = useState('');
    const [fileType, setFileType] = useState('pdf'); // 'pdf' | 'text' | 'markdown'
    const [textPages, setTextPages] = useState([]); // sentences[][] for .txt files
    const [markdownPages, setMarkdownPages] = useState([]); // page objects for .md files
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
    // and markdown we slice the pre-paginated array.
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
            return;
        }
        if (fileType === 'markdown' && markdownPages.length > 0) {
            const page = markdownPages[currentPage - 1];
            const sentences = page?.sentences || [];
            setTextItems(sentences);
            sentenceRefs.current = sentences.map(() => null);
            setStatus(`Page ${currentPage} Ready`);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pdfDoc, currentPage, fileType, textPages, markdownPages]);

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
        setMarkdownPages([]);
        setTextPages(pages);
        setNumPages(pages.length);
        setFileType('text');
        setPdfFileName(fileName);
        applySavedProgress(fileName, pages.length);
    };

    // Load a markdown document: parse blocks, paginate paragraph-aware, then
    // hand off to the markdown renderer. Sentences for TTS already pass through
    // markdownToSpeech() during segmentation, so the audio path stays plain.
    const loadMarkdownDocument = (rawText, fileName) => {
        const blocks = segmentMarkdown(rawText);
        const pages = paginateMarkdownBlocks(blocks, SENTENCES_PER_TEXT_PAGE);
        setPdfDoc(null);
        setPdfOutline([]);
        setTextPages([]);
        setMarkdownPages(pages);
        setNumPages(pages.length);
        setFileType('markdown');
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
                    setMarkdownPages([]);
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

        if (detected === 'text' || detected === 'markdown') {
            const reader = new FileReader();
            reader.onload = (ev) => {
                try {
                    const rawText = ev.target.result;
                    if (detected === 'markdown') {
                        loadMarkdownDocument(rawText, fileName);
                    } else {
                        loadTextDocument(rawText, fileName);
                    }
                    saveBook(file, { page: 1, sentenceIndex: -1 }).then(() => {
                        getRecentBooks().then(setRecentBooks);
                    });
                } catch (e) {
                    console.error('Failed to load file:', e);
                    setStatus(detected === 'markdown' ? "Error loading markdown file" : "Error loading text file");
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

            if (storedType === 'text' || storedType === 'markdown') {
                const rawText = new TextDecoder().decode(bookData.data);
                if (storedType === 'markdown') {
                    loadMarkdownDocument(rawText, fileName);
                } else {
                    loadTextDocument(rawText, fileName);
                }
                updateBookMeta(fileName, {}).then(() => {
                    getRecentBooks().then(setRecentBooks);
                });
                return;
            }

            const loadingTask = pdfjsLibRef.current.getDocument({ data: bookData.data });
            const doc = await loadingTask.promise;

            setFileType('pdf');
            setTextPages([]);
            setMarkdownPages([]);
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

    // Current markdown page object (rawMarkdown + paragraphMap) — null for non-md files
    const markdownPageData = fileType === 'markdown' ? (markdownPages[currentPage - 1] || null) : null;

    // Extract chunks across the entire document for backend indexing. One row per
    // PDF/TXT page, one row per markdown block (code blocks excluded). Order
    // (`ord`) is monotonic across the whole doc; `page` is the 1-based page that
    // chunk belongs to so we can cite back to it in retrieval. Empty chunks are
    // skipped so we don't waste embedding compute on whitespace.
    const extractAllChunks = async () => {
        if (fileType === 'pdf') {
            if (!pdfDoc) return [];
            const out = [];
            let ord = 0;
            for (let p = 1; p <= pdfDoc.numPages; p++) {
                const page = await pdfDoc.getPage(p);
                const content = await page.getTextContent();
                const text = content.items
                    .map(i => i.str)
                    .join(' ')
                    .replace(/\s+/g, ' ')
                    .trim();
                if (text) out.push({ ord: ord++, page: p, chunk_type: 'page', text });
            }
            return out;
        }
        if (fileType === 'markdown') {
            const out = [];
            let ord = 0;
            markdownPages.forEach((pageObj, pi) => {
                for (const block of pageObj.blocks || []) {
                    if (block.type === 'code') continue;
                    const text = (block.plain || '').replace(/\s+/g, ' ').trim();
                    if (text) out.push({ ord: ord++, page: pi + 1, chunk_type: 'block', text });
                }
            });
            return out;
        }
        if (fileType === 'text') {
            const out = [];
            let ord = 0;
            textPages.forEach((sentences, pi) => {
                const text = (sentences || []).join(' ').replace(/\s+/g, ' ').trim();
                if (text) out.push({ ord: ord++, page: pi + 1, chunk_type: 'page', text });
            });
            return out;
        }
        return [];
    };

    // Close the currently open document and drop back to the welcome screen.
    // Reading progress was already persisted by the progress effect, so the
    // doc will resume at the right spot the next time the user opens it.
    const closeDocument = () => {
        setPdfDoc(null);
        setPdfFileName('');
        setFileType('pdf');
        setNumPages(0);
        setCurrentPage(1);
        setTextItems([]);
        setTextPages([]);
        setMarkdownPages([]);
        setPdfOutline([]);
        setCurrentSentenceIndex(-1);
        sentenceRefs.current = [];
        playbackIndexRef.current = -1;
        setStatus('Ready to Open PDF');
        // Refresh the library snapshot so the welcome screen shows the latest
        // lastOpened ordering when the user lands back on it.
        getRecentBooks().then(setRecentBooks).catch(() => {});
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
        markdownPageData,

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
        extractAllChunks,
        closeDocument,
    };
}
