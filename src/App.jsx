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

// Components
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import PdfViewer from './components/PdfViewer';
import ChatView from './components/ChatView';
import ChatSidebar from './components/ChatSidebar';
import MobileBottomNav from './components/MobileBottomNav';

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
    handlePlayPause, stopPlayback, skipToNextSentence,
    readSelection, stopSelectionRead,
    previewVoice, stopVoicePreview, downloadPageAudio, clearCache,
    synthesizeText, playChatUrl, playChatSpeech, stopChatPlayback,
  } = ttsEngine;

  const chatEngine = useChatEngine({
    ollamaHost, ollamaPort, selectedModel,
    chatTtsMode, chatAutoTts, enableThinking,
    isLocalhost, selectedVoice, playbackSpeed, requestTimeout,
    apiHost, apiPort,
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
      if (docIndexByDocId[docId]) return; // already cached
      try {
        const res = await fetch(getApiUrl(`/v1/docs/${encodeURIComponent(docId)}`));
        if (cancelled) return;
        if (res.status === 404) {
          setDocIndexByDocId((prev) => ({ ...prev, [docId]: { state: 'idle' } }));
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
        state: lastStatus?.state || 'chunks_uploaded',
        chunkCount: lastStatus?.chunk_count ?? chunks.length,
        embeddedCount: lastStatus?.embedded_count ?? 0,
      },
    }));
    showToast(`Uploaded ${chunks.length} chunks. Embeddings come in PR 4.`, 4000);
  }, [pdfFileName, ensureDocHash, extractAllChunks, fileType, numPages, showToast, apiHost, apiPort]);

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
      />

      <KeyboardShortcutsModal show={showShortcuts} theme={theme} onClose={() => setShowShortcuts(false)} />

      <main className="flex-1 flex overflow-hidden relative">
        {/* Mobile sidebar overlay */}
        {sidebarOpen && effectiveIsMobile && (
          <div
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-30 md:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {inChat ? (
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
        )}

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
            showToast={showToast}
            pendingDocContext={pendingChatContext}
            clearPendingDocContext={clearPendingChatContext}
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
        />
        )}
      </main>

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
    </div>
  );
}