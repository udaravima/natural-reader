import { useState, useEffect, useRef, useCallback } from 'react';
import { Loader2 } from 'lucide-react';

// Hooks
import { usePersistedState } from './hooks/usePersistedState';
import { useMobileDetect } from './hooks/useMobileDetect';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { useTheme } from './hooks/useTheme';
import { usePdfEngine } from './hooks/usePdfEngine';
import { useTtsEngine } from './hooks/useTtsEngine';
import { useChatEngine } from './hooks/useChatEngine';

// Constants
import { OLLAMA_DEFAULTS } from './constants';

// Utils
import { buildApiUrl } from './utils/url';
import { getOrComputeDocHash } from './utils/docHash';
import { getBook } from './db';
import { uploadPdfBytesToBackend } from './lib/uploadPdf';

// Components
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import PdfViewer from './components/PdfViewer';
import ChatView from './components/ChatView';
import ChatSidebar from './components/ChatSidebar';
import MobileBottomNav from './components/MobileBottomNav';
import DoclingConvertDialog from './components/DoclingConvertDialog';

// Overlays
import DragOverlay from './components/overlays/DragOverlay';
import ToastNotification from './components/overlays/ToastNotification';
import ContextMenu from './components/overlays/ContextMenu';
import KeyboardShortcutsModal from './components/overlays/KeyboardShortcutsModal';
import ReadSelectionButton from './components/overlays/ReadSelectionButton';

export default function App() {
  // --- PERSISTED SETTINGS ---
  const [darkMode, setDarkMode] = usePersistedState('darkMode', false);
  const [volume, setVolume] = usePersistedState('volume', 1.0);
  const [scale, setScale] = usePersistedState('scale', 1.2);
  const [playbackSpeed, setPlaybackSpeed] = usePersistedState('playbackSpeed', 1.0);
  const [selectedVoice, setSelectedVoice] = usePersistedState('selectedVoice', 'af_heart');
  const [isLocalhost, setIsLocalhost] = usePersistedState('isLocalhost', true);
  const [apiHost, setApiHost] = usePersistedState('apiHost', 'localhost');
  const [apiPort, setApiPort] = usePersistedState('apiPort', '8000');
  const [requestTimeout, setRequestTimeout] = usePersistedState('requestTimeout', 15);
  const [unlimitedBatchTimeout, setUnlimitedBatchTimeout] = usePersistedState('unlimitedBatchTimeout', true);
  const [mobileBreakpoint, setMobileBreakpoint] = usePersistedState('mobileBreakpoint', 768);
  const [layoutMode, setLayoutMode] = usePersistedState('layoutMode', 'auto');
  const [showHeaderControlsOnMobile, setShowHeaderControlsOnMobile] = usePersistedState('showHeaderControlsOnMobile', false);
  // Reader / Chat top-level view + Ollama config
  const [viewMode, setViewMode] = usePersistedState('viewMode', 'reader');
  const [ollamaHost, setOllamaHost] = usePersistedState('ollamaHost', OLLAMA_DEFAULTS.host);
  const [ollamaPort, setOllamaPort] = usePersistedState('ollamaPort', OLLAMA_DEFAULTS.port);
  const [selectedModel, setSelectedModel] = usePersistedState('selectedModel', '');
  const [chatTtsMode, setChatTtsMode] = usePersistedState('chatTtsMode', 'streaming');
  const [chatAutoTts, setChatAutoTts] = usePersistedState('chatAutoTts', true);
  const [enableThinking, setEnableThinking] = usePersistedState('enableThinking', false);
  // Distraction-free reading: hides Header, sidebars, mobile bottom nav, and
  // the PdfViewer toolbar so only the page content + a small floating exit
  // pill remain. Persisted across reloads (some users prefer the immersive
  // layout as their default).
  const [distractionFree, setDistractionFree] = usePersistedState('distractionFree', false);

  // --- TRANSIENT UI STATE ---
  const [status, setStatus] = useState('Initializing PDF Engine...');
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [backendAvailable, setBackendAvailable] = useState(null);
  const [toastMessage, setToastMessage] = useState(null);

  // Centralized toast helper — avoids scattered setTimeout patterns
  const showToast = useCallback((message, duration = 4000) => {
    setToastMessage(message);
    setTimeout(() => setToastMessage(null), duration);
  }, []);
  const [contextMenu, setContextMenu] = useState(null);
  const [sidebarTab, setSidebarTab] = useState('sentences');
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Context payload waiting to be attached to the next chat message.
  // Lives at App scope so it survives the reader→chat view-mode switch.
  // Shape: { doc_id, fileName, page, kind: 'page'|'selection', text }
  const [pendingChatContext, setPendingChatContext] = useState(null);

  // Per-document index status keyed by sha256 doc_id. Shape:
  //   { state: 'idle' | 'chunks_uploaded' | 'indexing' | 'indexed' | 'failed' | 'uploading',
  //     chunkCount, embeddedCount }
  const [docIndexByDocId, setDocIndexByDocId] = useState({});
  // Computed hash for the currently open document — null until lazily hashed.
  const [currentDocId, setCurrentDocId] = useState(null);

  // Per-document docling conversion status. Shape mirrors index:
  //   { state: 'idle' | 'uploading' | 'converting' | 'converted' | 'failed',
  //     pageCount, error, options }
  const [docConvertByDocId, setDocConvertByDocId] = useState({});
  // Whether the reader is showing the original PDF canvas or the converted MD.
  // Keyed by doc_id so the choice survives doc switches.
  const [docViewByDocId, setDocViewByDocId] = useState({});
  // Modal visibility for the docling options dialog.
  const [convertDialogOpen, setConvertDialogOpen] = useState(false);

  const pdfContainerRef = useRef(null);

  // --- HOOKS ---
  const theme = useTheme(darkMode);

  const { effectiveIsMobile, sidebarOpen, setSidebarOpen } = useMobileDetect(mobileBreakpoint, layoutMode);

  const pdfEngine = usePdfEngine({ scale, setStatus, setToastMessage });

  const {
    pdfDoc, pdfFileName, fileType, currentPage, setCurrentPage, numPages,
    textItems, isLibLoaded, pdfOutline, recentBooks,
    currentSentenceIndex, setCurrentSentenceIndex,
    canvasRef, textLayerRef, fileInputRef, sentenceRefs, playbackIndexRef,
    processFile, openFromLibrary, removeFromLibrary, handleFileUpload,
    calculateReadingProgress, calculateEstimatedTimeRemaining,
    markdownPageData,
    extractAllChunks,
    closeDocument,
  } = pdfEngine;

  const inChat = viewMode === 'chat';

  const ttsEngine = useTtsEngine({
    textItems, currentSentenceIndex, setCurrentSentenceIndex,
    playbackIndexRef, currentPage, setCurrentPage, numPages,
    selectedVoice, playbackSpeed, isLocalhost, volume,
    apiHost, apiPort, requestTimeout, unlimitedBatchTimeout,
    backendAvailable, pdfFileName,
    setStatus, showToast,
    enabled: !inChat,
  });

  const {
    isPlaying, isDownloading, isReadingSelection, isPreviewingVoice,
    downloadingMessageId, bookProgress,
    handlePlayPause, stopPlayback, skipToNextSentence,
    readSelection, stopSelectionRead,
    previewVoice, stopVoicePreview, downloadPageAudio, downloadTextAudio,
    downloadBookAudio, cancelBookDownload, clearCache,
    synthesizeText, playChatUrl, playChatSpeech, stopChatPlayback,
  } = ttsEngine;

  // currentDocId / currentDocIndexState are exposed to the chat engine so the
  // tool registry can decide whether to advertise `search_document` to the
  // model. Both are null while no doc is open.
  const currentDocIndexEntry = currentDocId ? docIndexByDocId[currentDocId] : null;
  const chatEngine = useChatEngine({
    ollamaHost, ollamaPort, selectedModel,
    chatTtsMode, chatAutoTts, enableThinking,
    isLocalhost, selectedVoice, playbackSpeed, requestTimeout,
    apiHost, apiPort,
    currentDocId, currentDocIndexState: currentDocIndexEntry?.state || null,
    synthesizeText, playChatUrl, playChatSpeech, stopChatPlayback,
    showToast,
  });

  const {
    messages: chatMessages,
    isStreaming: chatIsStreaming,
    availableModels,
    reachable: ollamaReachable,
    sendMessage: chatSendMessage,
    stopStream: chatStopStream,
    clearHistory: chatClearHistory,
    refreshModels,
    speakingMessageId,
    speakMessage,
    stopSpeaking,
    sessions: chatSessions,
    activeSessionId: chatActiveSessionId,
    events: chatEvents,
    newSession: chatNewSession,
    switchToSession: chatSwitchToSession,
    deleteSession: chatDeleteSession,
    renameSession: chatRenameSession,
  } = chatEngine;

  useKeyboardShortcuts({
    handlePlayPause, stopPlayback, skipToNextSentence,
    setCurrentSentenceIndex, setCurrentPage, setScale, setDarkMode,
    setDistractionFree,
    numPages,
    viewMode,
  });

  // Stop chat audio + abort any in-flight stream when leaving chat mode.
  useEffect(() => {
    if (!inChat) {
      chatStopStream();
      stopChatPlayback();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inChat]);

  // True when any document (PDF, .txt, or .md) is loaded — used as a gate by UI
  // bits that don't care about file type. pdfDoc stays the source of truth for
  // code that actually needs the pdf.js object (e.g. chapter navigation).
  const hasDocument = !!pdfDoc || ((fileType === 'text' || fileType === 'markdown') && numPages > 0);

  // --- BACKEND HEALTH CHECK ---
  const getApiUrl = (endpoint) => buildApiUrl(apiHost, apiPort, endpoint);

  // Navigation helpers (used by PdfViewer, MobileBottomNav, keyboard shortcuts)
  const goToNextPage = useCallback(() => setCurrentPage(p => Math.min(numPages, p + 1)), [numPages, setCurrentPage]);
  const goToPrevPage = useCallback(() => setCurrentPage(p => Math.max(1, p - 1)), [setCurrentPage]);
  const skipToPrevSentence = useCallback(() => setCurrentSentenceIndex(prev => Math.max(-1, prev - 1)), [setCurrentSentenceIndex]);

  const checkBackend = async () => {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);
      const response = await fetch(getApiUrl('/v1/health'), {
        method: 'GET',
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      if (response.ok) {
        const data = await response.json();
        if (data.status === 'ok') {
          setBackendAvailable(true);
          return true;
        }
      }
      throw new Error('Backend error');
    } catch (e) {
      console.warn('Backend not available:', e.message);
      setBackendAvailable(false);
      showToast('Kokoro backend not detected. Using browser voice.', 5000);
      return false;
    }
  };

  // Initial backend check
  useEffect(() => {
    checkBackend();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-switch to chapters tab when outline is available
  useEffect(() => {
    if (pdfOutline.length > 0) {
      setSidebarTab('chapters');
    }
  }, [pdfOutline]);

  // Close context menu when clicking elsewhere
  useEffect(() => {
    const handleClick = () => setContextMenu(null);
    if (contextMenu) {
      window.addEventListener('click', handleClick);
      return () => window.removeEventListener('click', handleClick);
    }
  }, [contextMenu]);

  // --- EVENT HANDLERS ---
  // In chat mode, ChatView owns drag/drop for image/audio attachments. We bail
  // out at the window level so the reader's DragOverlay never appears and the
  // file isn't routed through processFile() (which expects PDF/TXT).
  const handleDragOver = (e) => {
    if (inChat) return;
    e.preventDefault(); e.stopPropagation(); setIsDragging(true);
  };
  const handleDragLeave = (e) => {
    if (inChat) return;
    e.preventDefault(); e.stopPropagation(); setIsDragging(false);
  };
  const handleDrop = (e) => {
    if (inChat) return;
    e.preventDefault(); e.stopPropagation(); setIsDragging(false);
    const files = e.dataTransfer.files;
    if (files.length === 0) return;
    const file = files[0];
    const name = (file.name || '').toLowerCase();
    const isSupported =
      file.type === 'application/pdf' ||
      file.type === 'text/plain' ||
      file.type === 'text/markdown' ||
      name.endsWith('.pdf') ||
      name.endsWith('.txt') ||
      name.endsWith('.md') ||
      name.endsWith('.markdown');
    if (isSupported) {
      processFile(file);
    } else {
      setStatus("Please drop a PDF, TXT, or Markdown file");
    }
  };

  const handleMobileSentenceClick = (index) => {
    setCurrentSentenceIndex(index - 1);
    ttsEngine.setIsPlaying(true);
    if (effectiveIsMobile) setSidebarOpen(false);
  };

  const handleSentenceContextMenu = (e, sentenceIndex) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, sentenceIndex });
  };

  const continueFromHere = (sentenceIndex) => {
    setCurrentSentenceIndex(sentenceIndex - 1);
    playbackIndexRef.current = sentenceIndex - 1;
    ttsEngine.setIsPlaying(true);
    setContextMenu(null);
    setStatus(`Starting from sentence ${sentenceIndex + 1}`);
  };

  const handleChapterNavigation = (pageNum, title) => {
    setCurrentPage(pageNum);
    setCurrentSentenceIndex(-1);
    setStatus(`Jumped to: ${title}`);
  };

  // ---------- DOC-AWARE CHAT HANDLERS ----------
  // Cap the excerpt before sending — small models choke on a wall of text and
  // even big ones waste tokens on a whole dense PDF page.
  const CONTEXT_CHAR_CAP = 8000;

  // Read the file bytes back out of IndexedDB and compute (or look up) its
  // sha256 hash. Returns null if the doc isn't in the library yet.
  const ensureDocHash = useCallback(async () => {
    if (!pdfFileName) return null;
    const record = await getBook(pdfFileName);
    if (!record?.data) return null;
    return getOrComputeDocHash(pdfFileName, record.data);
  }, [pdfFileName]);

  const handleAskAboutPage = useCallback(async (page) => {
    if (!pdfFileName) return;
    // textItems holds the sentences for the currently rendered page across PDF,
    // text, and markdown — joining gives us the full page text.
    const fullText = (textItems || []).join(' ').trim();
    if (!fullText) {
      showToast('Nothing to ask about — page has no text.', 3000);
      return;
    }
    const text = fullText.length > CONTEXT_CHAR_CAP
      ? fullText.slice(0, CONTEXT_CHAR_CAP) + ' [truncated]'
      : fullText;
    const docId = await ensureDocHash();
    setPendingChatContext({
      doc_id: docId,
      fileName: pdfFileName,
      page,
      kind: 'page',
      text,
    });
    setViewMode('chat');
  }, [pdfFileName, textItems, ensureDocHash, setViewMode, showToast]);

  const handleAskAboutSelection = useCallback(async () => {
    const sel = (typeof window !== 'undefined') ? window.getSelection() : null;
    const selectedText = sel ? sel.toString().trim() : '';
    if (!selectedText) {
      showToast('Select some text first, then click again.', 3000);
      return;
    }
    if (!pdfFileName) return;
    const text = selectedText.length > CONTEXT_CHAR_CAP
      ? selectedText.slice(0, CONTEXT_CHAR_CAP) + ' [truncated]'
      : selectedText;
    const docId = await ensureDocHash();
    setPendingChatContext({
      doc_id: docId,
      fileName: pdfFileName,
      page: currentPage,
      kind: 'selection',
      text,
    });
    setViewMode('chat');
  }, [pdfFileName, currentPage, ensureDocHash, setViewMode, showToast]);

  const clearPendingChatContext = useCallback(() => setPendingChatContext(null), []);

  // Synthesize the WHOLE document as a single audiobook .wav.
  // Pulls per-page text via the existing extractAllChunks helper (works for
  // PDF, TXT, and MD), then hands the page list to the TTS engine which
  // synthesizes one batch per page and concatenates the WAVs client-side.
  const handleDownloadBookAudio = useCallback(async () => {
    if (!pdfFileName) return;
    if (!isLocalhost) {
      showToast('Audiobook export requires the Kokoro backend.', 5000);
      return;
    }
    let chunks;
    try {
      chunks = await extractAllChunks();
    } catch (e) {
      console.error('Chunk extraction failed:', e);
      showToast(`Could not extract text: ${e.message}`, 5000);
      return;
    }
    if (!chunks || chunks.length === 0) {
      showToast('No extractable text in this document.', 4000);
      return;
    }
    // extractAllChunks returns one chunk per page for PDF/TXT and one per
    // block for MD. Group by page so each `downloadBookAudio` iteration is a
    // page-sized batch — a single MD page can have many blocks.
    const byPage = new Map();
    for (const c of chunks) {
      const p = c.page ?? 0;
      const prev = byPage.get(p) || '';
      byPage.set(p, prev ? `${prev}\n\n${c.text}` : c.text);
    }
    const pages = Array.from(byPage.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([page, text]) => ({ page, text }));

    const base = pdfFileName.replace(/\.(pdf|txt|md|markdown)$/i, '');
    await downloadBookAudio(pages, `${base}_audiobook`);
  }, [pdfFileName, isLocalhost, extractAllChunks, downloadBookAudio, showToast]);

  // Synthesize a single chat message into a downloadable .wav. Filename is
  // derived from the active session title and message position so multiple
  // exports from the same chat don't clobber each other in the user's
  // Downloads folder.
  const handleDownloadMessageAudio = useCallback((messageId) => {
    const msg = chatMessages.find((m) => m.id === messageId);
    if (!msg || !msg.content) return;
    const session = chatSessions.find((s) => s.id === chatActiveSessionId);
    const sessionTitle = session?.title || 'chat';
    const index = chatMessages
      .filter((m) => m.role === 'assistant')
      .findIndex((m) => m.id === messageId);
    const seq = index >= 0 ? `_msg${index + 1}` : '';
    const base = `${sessionTitle}${seq}`;
    downloadTextAudio(msg.content, base, messageId);
  }, [chatMessages, chatSessions, chatActiveSessionId, downloadTextAudio]);

  // Close the active document and return to the library/welcome screen.
  // Stops TTS first so audio doesn't keep playing into nothingness, then
  // clears the in-flight doc id (the index/convert toolbar disappears when
  // hasDocument flips to false).
  const handleGoHome = useCallback(() => {
    stopPlayback();
    stopChatPlayback();
    closeDocument();
    setCurrentDocId(null);
    setPendingChatContext(null);
  }, [stopPlayback, stopChatPlayback, closeDocument]);

  // ---------- INDEXING ----------
  // When the open document changes, lazily hash it and fetch its backend
  // status so the toolbar button shows the right label on load.
  useEffect(() => {
    let cancelled = false;
    if (!pdfFileName) {
      setCurrentDocId(null);
      return undefined;
    }
    (async () => {
      const docId = await ensureDocHash();
      if (cancelled || !docId) return;
      setCurrentDocId(docId);
      if (docIndexByDocId[docId] && docConvertByDocId[docId]) return; // already cached
      try {
        const res = await fetch(getApiUrl(`/v1/docs/${encodeURIComponent(docId)}`));
        if (cancelled) return;
        if (res.status === 404) {
          setDocIndexByDocId((prev) => ({ ...prev, [docId]: { state: 'idle' } }));
          setDocConvertByDocId((prev) => ({ ...prev, [docId]: { state: 'idle' } }));
          return;
        }
        if (!res.ok) return; // backend offline; leave state undefined
        const data = await res.json();
        setDocIndexByDocId((prev) => ({
          ...prev,
          [docId]: {
            state: data.state || 'idle',
            chunkCount: data.chunk_count,
            embeddedCount: data.embedded_count,
          },
        }));
        setDocConvertByDocId((prev) => ({
          ...prev,
          [docId]: {
            // Backend null → 'idle' so the button shows the inviting "Convert" label.
            state: data.conversion_state || 'idle',
            pageCount: data.converted_page_count || 0,
            error: data.conversion_error || null,
            options: data.conversion_options || null,
            hasPdf: !!data.has_pdf,
          },
        }));
      } catch {
        // Backend unreachable — leave entry undefined. Clicking Index later
        // will attempt the network call and surface a toast.
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfFileName, ensureDocHash]);

  const handleIndexDocument = useCallback(async () => {
    if (!pdfFileName) return;
    const docId = await ensureDocHash();
    if (!docId) {
      showToast('Could not read document bytes — re-open the file and try again.', 4000);
      return;
    }
    setDocIndexByDocId((prev) => ({ ...prev, [docId]: { ...(prev[docId] || {}), state: 'uploading' } }));
    const apiUrl = (path) => buildApiUrl(apiHost, apiPort, path);

    // 1. Register the document.
    let registerRes;
    try {
      const fileSize = (await getBook(pdfFileName))?.size ?? 0;
      registerRes = await fetch(apiUrl('/v1/docs'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          doc_id: docId,
          file_name: pdfFileName,
          file_type: fileType,
          size_bytes: fileSize,
          page_count: numPages,
        }),
      });
      if (!registerRes.ok) throw new Error(`HTTP ${registerRes.status}`);
    } catch (e) {
      console.error('Doc register failed:', e);
      setDocIndexByDocId((prev) => ({ ...prev, [docId]: { ...(prev[docId] || {}), state: 'failed' } }));
      showToast(`Indexing failed: backend unreachable (${e.message})`, 5000);
      return;
    }

    // 2. Extract chunks from the loaded document.
    let chunks;
    try {
      chunks = await extractAllChunks();
    } catch (e) {
      console.error('Chunk extraction failed:', e);
      setDocIndexByDocId((prev) => ({ ...prev, [docId]: { ...(prev[docId] || {}), state: 'failed' } }));
      showToast(`Indexing failed: could not extract text (${e.message})`, 5000);
      return;
    }
    if (chunks.length === 0) {
      setDocIndexByDocId((prev) => ({ ...prev, [docId]: { state: 'idle' } }));
      showToast('Nothing to index — this document has no extractable text.', 4000);
      return;
    }

    // 3. Upload in batches of 50 to keep request payloads bounded and give
    // the UI a chance to show progress on large docs.
    const BATCH = 50;
    let lastStatus = null;
    try {
      for (let i = 0; i < chunks.length; i += BATCH) {
        const slice = chunks.slice(i, i + BATCH);
        const res = await fetch(apiUrl(`/v1/docs/${encodeURIComponent(docId)}/chunks`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chunks: slice }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        lastStatus = data.status;
      }
    } catch (e) {
      console.error('Chunk upload failed:', e);
      setDocIndexByDocId((prev) => ({ ...prev, [docId]: { ...(prev[docId] || {}), state: 'failed' } }));
      showToast(`Indexing failed during upload: ${e.message}`, 5000);
      return;
    }

    setDocIndexByDocId((prev) => ({
      ...prev,
      [docId]: {
        state: 'indexing',
        chunkCount: lastStatus?.chunk_count ?? chunks.length,
        embeddedCount: lastStatus?.embedded_count ?? 0,
      },
    }));

    // 4. Kick off the embedding job. Returns 202 immediately; we poll status.
    try {
      const indexRes = await fetch(apiUrl(`/v1/docs/${encodeURIComponent(docId)}/index`), { method: 'POST' });
      if (!indexRes.ok) throw new Error(`HTTP ${indexRes.status}`);
    } catch (e) {
      console.error('Index kick-off failed:', e);
      setDocIndexByDocId((prev) => ({ ...prev, [docId]: { ...(prev[docId] || {}), state: 'failed' } }));
      showToast(`Could not start embedding job: ${e.message}`, 5000);
      return;
    }

    showToast(`Embedding ${chunks.length} chunks — this can take a minute.`, 3500);

    // 5. Poll every 2s until state leaves 'indexing'. Cap the polling window
    // to ~10 minutes so a stuck job doesn't loop forever.
    const POLL_MS = 2000;
    const MAX_POLLS = 300;
    for (let i = 0; i < MAX_POLLS; i++) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      try {
        const sRes = await fetch(apiUrl(`/v1/docs/${encodeURIComponent(docId)}`));
        if (!sRes.ok) continue;
        const sData = await sRes.json();
        setDocIndexByDocId((prev) => ({
          ...prev,
          [docId]: {
            state: sData.state,
            chunkCount: sData.chunk_count,
            embeddedCount: sData.embedded_count,
          },
        }));
        if (sData.state === 'indexed') {
          showToast(`Indexed ${sData.embedded_count} chunks.`, 3000);
          return;
        }
        if (sData.state === 'failed') {
          showToast(`Indexing failed: ${sData.error_message || 'unknown error'}`, 6000);
          return;
        }
      } catch {
        // Backend hiccup — keep polling; transient errors shouldn't bail.
      }
    }
    showToast('Indexing is taking unusually long — check the server logs.', 6000);
  }, [pdfFileName, ensureDocHash, extractAllChunks, fileType, numPages, showToast, apiHost, apiPort]);

  // ---------- DOCLING CONVERSION ----------
  // Mirror of handleIndexDocument: registers (if needed) → uploads PDF bytes →
  // starts /convert → polls until conversion+indexing finish. Auto-switches
  // the reader to the MD view on success.
  const handleConvertDocument = useCallback(async (options) => {
    if (!pdfFileName || fileType !== 'pdf') return;
    const docId = await ensureDocHash();
    if (!docId) {
      showToast('Could not read document bytes — re-open the file and try again.', 4000);
      return;
    }
    const apiUrl = (path) => buildApiUrl(apiHost, apiPort, path);

    setDocConvertByDocId((prev) => ({
      ...prev,
      [docId]: { ...(prev[docId] || {}), state: 'uploading', error: null },
    }));

    // 1. Register the doc (idempotent).
    try {
      const fileSize = (await getBook(pdfFileName))?.size ?? 0;
      const registerRes = await fetch(apiUrl('/v1/docs'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          doc_id: docId,
          file_name: pdfFileName,
          file_type: fileType,
          size_bytes: fileSize,
          page_count: numPages,
        }),
      });
      if (!registerRes.ok) throw new Error(`HTTP ${registerRes.status}`);
    } catch (e) {
      console.error('Doc register failed:', e);
      setDocConvertByDocId((prev) => ({
        ...prev,
        [docId]: { ...(prev[docId] || {}), state: 'failed', error: e.message },
      }));
      showToast(`Convert failed: backend unreachable (${e.message})`, 5000);
      return;
    }

    // 2. Push the raw PDF bytes so the backend can run docling against them.
    try {
      await uploadPdfBytesToBackend({ docId, fileName: pdfFileName, apiHost, apiPort });
    } catch (e) {
      console.error('PDF upload failed:', e);
      setDocConvertByDocId((prev) => ({
        ...prev,
        [docId]: { ...(prev[docId] || {}), state: 'failed', error: e.message },
      }));
      showToast(`Could not upload PDF: ${e.message}`, 6000);
      return;
    }

    // 3. Kick off the conversion job.
    setDocConvertByDocId((prev) => ({
      ...prev,
      [docId]: { ...(prev[docId] || {}), state: 'converting', options, error: null },
    }));
    try {
      const res = await fetch(apiUrl(`/v1/docs/${encodeURIComponent(docId)}/convert`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(options),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const data = await res.json();
          if (data?.detail) detail = data.detail;
        } catch {
          /* keep status code */
        }
        throw new Error(detail);
      }
    } catch (e) {
      console.error('Convert kick-off failed:', e);
      setDocConvertByDocId((prev) => ({
        ...prev,
        [docId]: { ...(prev[docId] || {}), state: 'failed', error: e.message },
      }));
      showToast(`Could not start conversion: ${e.message}`, 6000);
      return;
    }

    showToast('Converting with Docling — this can take a few minutes.', 4000);

    // 4. Poll. Conversion finishes when conversion_state='converted' AND the
    // chained indexing leaves state='indexed' (or 'failed' on either side).
    const POLL_MS = 2000;
    const MAX_POLLS = 600; // 20 minutes — docling on big PDFs is slow.
    for (let i = 0; i < MAX_POLLS; i++) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      try {
        const sRes = await fetch(apiUrl(`/v1/docs/${encodeURIComponent(docId)}`));
        if (!sRes.ok) continue;
        const sData = await sRes.json();
        setDocConvertByDocId((prev) => ({
          ...prev,
          [docId]: {
            ...(prev[docId] || {}),
            state: sData.conversion_state || 'idle',
            pageCount: sData.converted_page_count || 0,
            error: sData.conversion_error || null,
            options: sData.conversion_options || prev[docId]?.options || null,
            hasPdf: !!sData.has_pdf,
          },
        }));
        setDocIndexByDocId((prev) => ({
          ...prev,
          [docId]: {
            state: sData.state || 'idle',
            chunkCount: sData.chunk_count,
            embeddedCount: sData.embedded_count,
          },
        }));
        if (sData.conversion_state === 'conversion_failed') {
          showToast(`Conversion failed: ${sData.conversion_error || 'unknown error'}`, 6000);
          return;
        }
        if (sData.conversion_state === 'converted' && sData.state === 'indexed') {
          showToast(`Converted ${sData.converted_page_count} pages.`, 3000);
          setDocViewByDocId((prev) => ({ ...prev, [docId]: 'md' }));
          return;
        }
        if (sData.conversion_state === 'converted' && sData.state === 'failed') {
          // Conversion worked but downstream embedding failed.
          showToast(`Conversion done, but indexing failed: ${sData.error_message || 'unknown'}`, 6000);
          return;
        }
      } catch {
        // Transient hiccup — keep polling.
      }
    }
    showToast('Conversion is taking unusually long — check the server logs.', 6000);
  }, [pdfFileName, fileType, ensureDocHash, numPages, showToast, apiHost, apiPort]);

  const openConvertDialog = useCallback(() => setConvertDialogOpen(true), []);
  const closeConvertDialog = useCallback(() => setConvertDialogOpen(false), []);
  const submitConvertDialog = useCallback((options) => {
    setConvertDialogOpen(false);
    handleConvertDocument(options);
  }, [handleConvertDocument]);

  // Download the docling-converted Markdown for the current doc. Uses an
  // ObjectURL + temporary <a> rather than data: URIs so large MD files don't
  // bloat memory or hit URL length limits.
  const handleExportMarkdown = useCallback(async () => {
    if (!currentDocId) return;
    const apiUrl = (path) => buildApiUrl(apiHost, apiPort, path);
    try {
      const res = await fetch(apiUrl(`/v1/docs/${encodeURIComponent(currentDocId)}/markdown`));
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const data = await res.json();
          if (data?.detail) detail = data.detail;
        } catch {
          /* keep status */
        }
        throw new Error(detail);
      }
      const text = await res.text();
      const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const baseName = (pdfFileName || 'document').replace(/\.[^.]+$/, '');
      const a = document.createElement('a');
      a.href = url;
      a.download = `${baseName}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // The URL needs to outlive the click() handler in some browsers; release
      // it on the next macrotask.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) {
      console.error('Markdown export failed:', e);
      showToast(`Could not export Markdown: ${e.message}`, 5000);
    }
  }, [currentDocId, apiHost, apiPort, pdfFileName, showToast]);

  // Wipe the converted markdown + derived chunks for this doc. Keeps the row
  // and any retained PDF so reconversion is one click.
  const handleDeleteMarkdown = useCallback(async () => {
    if (!currentDocId) return;
    // Confirm before destroying — once chunks are gone the chat can't search
    // this doc until it's re-indexed or reconverted.
    const ok = typeof window !== 'undefined'
      ? window.confirm('Delete the converted Markdown and its embeddings? The PDF stays in your library.')
      : true;
    if (!ok) return;

    const apiUrl = (path) => buildApiUrl(apiHost, apiPort, path);
    try {
      const res = await fetch(apiUrl(`/v1/docs/${encodeURIComponent(currentDocId)}/markdown`), {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      console.error('Markdown delete failed:', e);
      showToast(`Could not delete Markdown: ${e.message}`, 5000);
      return;
    }

    setDocConvertByDocId((prev) => ({
      ...prev,
      [currentDocId]: { ...(prev[currentDocId] || {}), state: 'idle', pageCount: 0, error: null, options: null },
    }));
    setDocIndexByDocId((prev) => ({
      ...prev,
      [currentDocId]: { state: 'idle', chunkCount: 0, embeddedCount: 0 },
    }));
    // If the user was reading the MD view, drop back to the PDF rendering.
    setDocViewByDocId((prev) => ({ ...prev, [currentDocId]: 'pdf' }));
    showToast('Converted Markdown deleted.', 3000);
  }, [currentDocId, apiHost, apiPort, showToast]);

  const currentConvertEntry = currentDocId ? docConvertByDocId[currentDocId] : null;
  const currentDocView = currentDocId ? docViewByDocId[currentDocId] || 'pdf' : 'pdf';
  const setCurrentDocView = useCallback((mode) => {
    if (!currentDocId) return;
    setDocViewByDocId((prev) => ({ ...prev, [currentDocId]: mode }));
  }, [currentDocId]);

  // --- LOADING STATE ---
  if (!isLibLoaded) {
    return (
      <div className={`h-screen w-full flex flex-col items-center justify-center ${theme.bg} gap-4`}>
        <Loader2 className="animate-spin text-blue-600" size={48} />
        <p className={`${theme.textSecondary} font-medium`}>Booting Neural Engine...</p>
      </div>
    );
  }

  // --- RENDER ---
  return (
    <div
      className={`flex flex-col h-screen ${theme.bg} ${theme.text} font-sans ${theme.selection} transition-colors duration-300`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <DragOverlay isDragging={isDragging} theme={theme} />

      {/* Reading Progress Bar */}
      {hasDocument && (
        <div className="h-1 bg-slate-300/20 w-full fixed top-0 left-0 z-50">
          <div
            className="h-full bg-gradient-to-r from-blue-500 via-cyan-500 to-teal-500 transition-all duration-500 ease-out"
            style={{ width: `${calculateReadingProgress()}%` }}
          />
        </div>
      )}

      <ToastNotification message={toastMessage} darkMode={darkMode} onClose={() => setToastMessage(null)} />

      <ContextMenu
        contextMenu={contextMenu}
        theme={theme}
        textItems={textItems}
        onContinueFromHere={continueFromHere}
        onCopySentence={() => showToast('Copied to clipboard', 2000)}
        onClose={() => setContextMenu(null)}
      />

      <ReadSelectionButton
        hasDocument={hasDocument}
        isReadingSelection={isReadingSelection}
        darkMode={darkMode}
        onReadSelection={readSelection}
        onStopSelectionRead={stopSelectionRead}
        onAskAboutSelection={inChat ? null : handleAskAboutSelection}
      />

      {!distractionFree && (
        <Header
          theme={theme}
          darkMode={darkMode}
          hasDocument={hasDocument}
          viewMode={viewMode} setViewMode={setViewMode}
          status={status}
          isPlaying={isPlaying}
          isLocalhost={isLocalhost} setIsLocalhost={setIsLocalhost}
          isDownloading={isDownloading}
          textItems={textItems}
          showHeaderControlsOnMobile={showHeaderControlsOnMobile}
          sidebarOpen={sidebarOpen} setSidebarOpen={setSidebarOpen}
          showShortcuts={showShortcuts} setShowShortcuts={setShowShortcuts}
          fileInputRef={fileInputRef}
          handlePlayPause={handlePlayPause}
          stopPlayback={stopPlayback}
          skipToNextSentence={skipToNextSentence}
          skipToPrevSentence={skipToPrevSentence}
          setDarkMode={setDarkMode}
          downloadPageAudio={downloadPageAudio}
          handleFileUpload={handleFileUpload}
          calculateEstimatedTimeRemaining={calculateEstimatedTimeRemaining}
          playbackSpeed={playbackSpeed}
          onGoHome={handleGoHome}
          onDownloadBookAudio={handleDownloadBookAudio}
          bookProgress={bookProgress}
          onCancelBookDownload={cancelBookDownload}
          onEnterDistractionFree={() => setDistractionFree(true)}
        />
      )}

      <KeyboardShortcutsModal show={showShortcuts} theme={theme} onClose={() => setShowShortcuts(false)} />

      <main className="flex-1 flex overflow-hidden relative">
        {/* Mobile sidebar overlay */}
        {sidebarOpen && effectiveIsMobile && (
          <div
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-30 md:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {!distractionFree && (inChat ? (
          <ChatSidebar
            theme={theme}
            darkMode={darkMode}
            effectiveIsMobile={effectiveIsMobile}
            sidebarOpen={sidebarOpen}
            ollamaHost={ollamaHost} setOllamaHost={setOllamaHost}
            ollamaPort={ollamaPort} setOllamaPort={setOllamaPort}
            selectedModel={selectedModel} setSelectedModel={setSelectedModel}
            availableModels={availableModels}
            reachable={ollamaReachable}
            refreshModels={refreshModels}
            chatTtsMode={chatTtsMode} setChatTtsMode={setChatTtsMode}
            chatAutoTts={chatAutoTts} setChatAutoTts={setChatAutoTts}
            enableThinking={enableThinking} setEnableThinking={setEnableThinking}
            messages={chatMessages}
            clearHistory={chatClearHistory}
            sessions={chatSessions}
            activeSessionId={chatActiveSessionId}
            events={chatEvents}
            newSession={chatNewSession}
            switchToSession={chatSwitchToSession}
            deleteSession={chatDeleteSession}
            renameSession={chatRenameSession}
          />
        ) : (
        <Sidebar
          theme={theme}
          darkMode={darkMode}
          effectiveIsMobile={effectiveIsMobile}
          sidebarOpen={sidebarOpen} setSidebarOpen={setSidebarOpen}
          settingsOpen={settingsOpen} setSettingsOpen={setSettingsOpen}
          sidebarTab={sidebarTab} setSidebarTab={setSidebarTab}
          selectedVoice={selectedVoice} setSelectedVoice={setSelectedVoice}
          playbackSpeed={playbackSpeed} setPlaybackSpeed={setPlaybackSpeed}
          volume={volume} setVolume={setVolume}
          isLocalhost={isLocalhost} setIsLocalhost={setIsLocalhost}
          apiHost={apiHost} setApiHost={setApiHost}
          apiPort={apiPort} setApiPort={setApiPort}
          requestTimeout={requestTimeout} setRequestTimeout={setRequestTimeout}
          unlimitedBatchTimeout={unlimitedBatchTimeout} setUnlimitedBatchTimeout={setUnlimitedBatchTimeout}
          backendAvailable={backendAvailable} setBackendAvailable={setBackendAvailable}
          layoutMode={layoutMode} setLayoutMode={setLayoutMode}
          mobileBreakpoint={mobileBreakpoint} setMobileBreakpoint={setMobileBreakpoint}
          showHeaderControlsOnMobile={showHeaderControlsOnMobile} setShowHeaderControlsOnMobile={setShowHeaderControlsOnMobile}
          isPreviewingVoice={isPreviewingVoice}
          previewVoice={previewVoice}
          stopVoicePreview={stopVoicePreview}
          hasDocument={hasDocument}
          pdfDoc={pdfDoc}
          pdfOutline={pdfOutline}
          textItems={textItems}
          currentSentenceIndex={currentSentenceIndex}
          sentenceRefs={sentenceRefs}
          clearCache={clearCache}
          checkBackend={checkBackend}
          setStatus={setStatus}
          calculateReadingProgress={calculateReadingProgress}
          handleMobileSentenceClick={handleMobileSentenceClick}
          handleSentenceContextMenu={handleSentenceContextMenu}
          handleChapterNavigation={handleChapterNavigation}
        />
        ))}

        {inChat ? (
          <ChatView
            theme={theme}
            darkMode={darkMode}
            effectiveIsMobile={effectiveIsMobile}
            messages={chatMessages}
            isStreaming={chatIsStreaming}
            selectedModel={selectedModel}
            reachable={ollamaReachable}
            sendMessage={chatSendMessage}
            stopStream={chatStopStream}
            speakingMessageId={speakingMessageId}
            speakMessage={speakMessage}
            stopSpeaking={stopSpeaking}
            downloadingMessageId={downloadingMessageId}
            downloadMessageAudio={isLocalhost ? handleDownloadMessageAudio : null}
            showToast={showToast}
            pendingDocContext={pendingChatContext}
            clearPendingDocContext={clearPendingChatContext}
            currentDocIndexState={pendingChatContext?.doc_id ? docIndexByDocId[pendingChatContext.doc_id]?.state : null}
          />
        ) : (
        <PdfViewer
          theme={theme}
          darkMode={darkMode}
          effectiveIsMobile={effectiveIsMobile}
          pdfDoc={pdfDoc}
          fileType={fileType}
          textItems={textItems}
          currentSentenceIndex={currentSentenceIndex}
          currentPage={currentPage} setCurrentPage={setCurrentPage}
          goToNextPage={goToNextPage} goToPrevPage={goToPrevPage}
          numPages={numPages}
          scale={scale} setScale={setScale}
          canvasRef={canvasRef}
          textLayerRef={textLayerRef}
          pdfContainerRef={pdfContainerRef}
          fileInputRef={fileInputRef}
          recentBooks={recentBooks}
          openFromLibrary={openFromLibrary}
          removeFromLibrary={removeFromLibrary}
          markdownPageData={markdownPageData}
          onAskAboutPage={handleAskAboutPage}
          indexEntry={currentDocId ? docIndexByDocId[currentDocId] : null}
          onIndexDocument={handleIndexDocument}
          docId={currentDocId}
          apiHost={apiHost}
          apiPort={apiPort}
          convertState={currentConvertEntry?.state || 'idle'}
          convertError={currentConvertEntry?.error}
          convertedPageCount={currentConvertEntry?.pageCount}
          onOpenConvertDialog={fileType === 'pdf' ? openConvertDialog : null}
          onExportMarkdown={handleExportMarkdown}
          onDeleteMarkdown={handleDeleteMarkdown}
          viewMode={currentDocView}
          setViewMode={setCurrentDocView}
          distractionFree={distractionFree}
        />
        )}
      </main>

      <DoclingConvertDialog
        theme={theme}
        darkMode={darkMode}
        open={convertDialogOpen}
        onClose={closeConvertDialog}
        onSubmit={submitConvertDialog}
        pageCount={numPages}
        initialOptions={currentConvertEntry?.options}
      />

      {!distractionFree && (
        <MobileBottomNav
          theme={theme}
          effectiveIsMobile={effectiveIsMobile}
          hasDocument={hasDocument && !inChat}
          currentPage={currentPage} setCurrentPage={setCurrentPage}
          numPages={numPages}
          currentSentenceIndex={currentSentenceIndex}
          textItems={textItems}
          isPlaying={isPlaying}
          handlePlayPause={handlePlayPause}
          skipToNextSentence={skipToNextSentence}
          skipToPrevSentence={skipToPrevSentence}
          goToNextPage={goToNextPage}
          goToPrevPage={goToPrevPage}
        />
      )}

      {/* Floating exit pill — only visible in distraction-free mode. Sits in
          the top-right so it doesn't fight reading flow and stays reachable
          by mouse + tap. F also toggles. */}
      {distractionFree && (
        <button
          onClick={() => setDistractionFree(false)}
          className="fixed top-3 right-3 z-50 px-3 py-1.5 rounded-full text-xs font-bold bg-slate-900/80 text-white shadow-lg backdrop-blur-sm hover:bg-slate-900 transition-colors flex items-center gap-1.5"
          title="Exit distraction-free mode (F)"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 14h6v6" />
            <path d="M20 10h-6V4" />
            <path d="m14 10 7-7" />
            <path d="M3 21l7-7" />
          </svg>
          Exit
        </button>
      )}
    </div>
  );
}