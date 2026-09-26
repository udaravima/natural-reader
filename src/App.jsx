import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Loader2 } from 'lucide-react';

// Hooks
import { usePersistedState, migratePersisted } from './hooks/usePersistedState';
import { useMobileDetect } from './hooks/useMobileDetect';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { useTheme } from './hooks/useTheme';
import { usePdfEngine } from './hooks/usePdfEngine';
import { useTtsEngine } from './hooks/useTtsEngine';
import { useChatEngine } from './hooks/useChatEngine';
import { useAuth } from './hooks/useAuth';
import { useViewModeGuard } from './hooks/useViewModeGuard';
import { useDocMetaPicker } from './hooks/useDocMetaPicker';
import { makePin } from './hooks/pins';

// Constants
import { OLLAMA_DEFAULTS } from './constants';
import { resolveForModel, patchForModel, migrateLegacyThinking } from './hooks/inference';

// Utils
import { apiFetch } from './utils/apiFetch';
import { getOrComputeDocHash } from './utils/docHash';
import { getBook } from './db';
import { saveWorkspaceState, clearWorkspaceState, getWorkspaceState } from './db';
import {
  registerDocument, linkDocToProject, parseTagsInput, requestReindex, deleteConvertedMarkdown,
} from './lib/docMeta';
import { describeRefusal } from './lib/apiErrors';
import { pollIndexUntilSettled, pollConvertUntilSettled } from './lib/docStatusPoller';
import { WorkspaceProvider } from './lib/WorkspaceContext';
import { createFsaWorkspace, createSnapshotWorkspace, pickEntryFile, isMarkdownPath } from './lib/workspace';

// Components
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import { AuthGate } from './components/auth/AuthGate';
import PdfViewer from './components/PdfViewer';
import ChatView from './components/ChatView';
import ChatSidebar from './components/ChatSidebar';
import { AdminConsole } from './components/admin/AdminConsole';
import LibraryPage from './components/library/LibraryPage';
import SettingsPage from './components/settings/SettingsPage';
import MobileBottomNav from './components/MobileBottomNav';
import DoclingConvertDialog from './components/DoclingConvertDialog';
import DistractionFreeBar from './components/DistractionFreeBar';

// Overlays
import DragOverlay from './components/overlays/DragOverlay';
import ToastNotification from './components/overlays/ToastNotification';
import ContextMenu from './components/overlays/ContextMenu';
import KeyboardShortcutsModal from './components/overlays/KeyboardShortcutsModal';
import ReadSelectionButton from './components/overlays/ReadSelectionButton';

export default function App() {
  // One-time migration: 'localhost' was the pre-auth default apiHost, but every
  // /v1 call now sends credentials — a cross-origin localhost:5173 →
  // localhost:8000 fetch is always CORS-blocked (credentials + wildcard origin
  // is rejected by browsers) and the OIDC session cookie only rides same-origin
  // requests. Blank = same-origin (Vite dev proxy / reverse proxy), the
  // supported setup. Must run before the usePersistedState hook reads the key.
  migratePersisted('apiHost', 'localhost', '');
  // Same trap, different spelling: '127.0.0.1' is also a cross-origin host for
  // the cookie (localhost ≠ 127.0.0.1), so it needs the same rewrite.
  migratePersisted('apiHost', '127.0.0.1', '');

  // --- PERSISTED SETTINGS ---
  const [darkMode, setDarkMode] = usePersistedState('darkMode', false);
  const [volume, setVolume] = usePersistedState('volume', 1.0);
  const [scale, setScale] = usePersistedState('scale', 1.2);
  const [playbackSpeed, setPlaybackSpeed] = usePersistedState('playbackSpeed', 1.0);
  const [selectedVoice, setSelectedVoice] = usePersistedState('selectedVoice', 'af_heart');
  const [isLocalhost, setIsLocalhost] = usePersistedState('isLocalhost', true);
  const [apiHost, setApiHost] = usePersistedState('apiHost', '');
  const [apiPort, setApiPort] = usePersistedState('apiPort', '8000');
  const auth = useAuth(apiHost, apiPort);
  const [requestTimeout, setRequestTimeout] = usePersistedState('requestTimeout', 15);
  const [unlimitedBatchTimeout, setUnlimitedBatchTimeout] = usePersistedState('unlimitedBatchTimeout', true);
  const [mobileBreakpoint, setMobileBreakpoint] = usePersistedState('mobileBreakpoint', 768);
  const [layoutMode, setLayoutMode] = usePersistedState('layoutMode', 'auto');
  const [showHeaderControlsOnMobile, setShowHeaderControlsOnMobile] = usePersistedState('showHeaderControlsOnMobile', false);
  // Reader / Chat top-level view + Ollama config
  const [viewMode, setViewMode] = usePersistedState('viewMode', 'reader');
  const [ollamaHost, setOllamaHost] = usePersistedState('ollamaHost', OLLAMA_DEFAULTS.host);
  const [ollamaPort, setOllamaPort] = usePersistedState('ollamaPort', OLLAMA_DEFAULTS.port);
  // Where inference runs: 'server' = authenticated /v1/inference gateway on
  // the backend, 'local' = browser→Ollama directly (pre-gateway behavior).
  const [inferenceSource, setInferenceSource] = usePersistedState('inferenceSource', 'server');
  const [selectedModel, setSelectedModel] = usePersistedState('selectedModel', '');
  const [chatTtsMode, setChatTtsMode] = usePersistedState('chatTtsMode', 'streaming');
  const [chatAutoTts, setChatAutoTts] = usePersistedState('chatAutoTts', true);
  // Composer in-progress state lives here (not in ChatView) so switching tabs —
  // which unmounts ChatView — doesn't discard a half-typed message. The text
  // draft is persisted (survives a reload too); pending image attachments are
  // in-memory only, to avoid packing base64 blobs into localStorage.
  const [chatDraft, setChatDraft] = usePersistedState('chatDraft', '');
  const [chatPendingAttachments, setChatPendingAttachments] = useState([]);
  // Per-model Ollama inference settings (context window, keep-alive, thinking
  // level, max reply tokens). Keyed by model name because a 9.7B and a 3B want
  // different context sizes on the same machine.
  const [inferenceByModel, setInferenceByModel] = usePersistedState('inferenceByModel', {});
  // Distraction-free reading: hides Header, sidebars, mobile bottom nav, and
  // the PdfViewer toolbar so only the page content + a small floating exit
  // pill remain. Persisted across reloads (some users prefer the immersive
  // layout as their default).
  const [distractionFree, setDistractionFree] = usePersistedState('distractionFree', false);

  // One-time seed: carry the old global `enableThinking` boolean into this
  // model's `think` setting the first time we see the model, so upgrading
  // users keep the thinking behaviour they had. Read straight from
  // localStorage rather than from React state — the `enableThinking` state is
  // deleted in Task 4 and this effect must keep working after that.
  useEffect(() => {
    if (!selectedModel) return;
    setInferenceByModel(prev => {
      if (prev[selectedModel]) return prev;
      let legacy = false;
      try {
        legacy = JSON.parse(localStorage.getItem('neural-pdf-enableThinking') || 'false');
      } catch { legacy = false; }
      return patchForModel(prev, selectedModel, { think: migrateLegacyThinking(legacy) });
    });
  }, [selectedModel, setInferenceByModel]);

  const inference = useMemo(
    () => resolveForModel(inferenceByModel, selectedModel),
    [inferenceByModel, selectedModel]
  );
  const setInference = useCallback(
    (patch) => {
      // With no model selected there is no key to patch under — writing
      // anyway would land the entry under "" (resolveForModel('', map) still
      // finds it, so the UI looks like it stuck) and orphan it in
      // localStorage the moment a real model is picked.
      if (!selectedModel) return;
      setInferenceByModel(prev => patchForModel(prev, selectedModel, patch));
    },
    [selectedModel, setInferenceByModel]
  );

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

  // Per-document index status keyed by sha256 doc_id. Shape:
  //   { state: 'idle' | 'uploading' | 'stored' | 'extracting' | 'extracted' | 'indexing' | 'indexed' | 'failed',
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

  // Project list for the optional register/upload picker (Task 9). Session-
  // level (not per-document): fetched once when signed in (below) — null while
  // loading, [] once loaded with no projects. The per-document picker *state*
  // lives in useDocMetaPicker, wired after pdfFileName is available.
  const [projects, setProjects] = useState(null);
  // Bumped when the Library creates a project, so the reader's picker refetches.
  const [projectsVersion, setProjectsVersion] = useState(0);

  const pdfContainerRef = useRef(null);
  const [workspace, setWorkspace] = useState(null);
  const workspaceRef = useRef(null);
  const [workspaceEntryPath, setWorkspaceEntryPath] = useState(null);
  const folderInputRef = useRef(null);
  const [reconnect, setReconnect] = useState(null); // { rootName } | null

  // Mirror workspace into a ref so async restore/reconnect effects can check
  // whether a manual open happened during the await without reading stale state.
  useEffect(() => { workspaceRef.current = workspace; }, [workspace]);

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
    loadMarkdownDocument,
    loadTextDocument,
  } = pdfEngine;

  // Per-document project/tags picker: resets whenever the loaded document
  // (pdfFileName) changes, so a selection made for one doc can't silently
  // carry over and mis-tag the next (Task 9 review).
  const {
    projectId: docProjectId, setProjectId: setDocProjectId,
    tagsText: docTagsText, setTagsText: setDocTagsText,
  } = useDocMetaPicker(pdfFileName);

  const inChat = viewMode === 'chat';
  const inAdmin = viewMode === 'admin';
  // Library, unlike admin, is available to any authenticated active user — it's
  // a capability-free view, listed in useViewModeGuard's CAP_FREE_VIEWS so the
  // guard doesn't bounce it to reader for lack of a matching capability.
  const inLibrary = viewMode === 'library';
  const inSettings = viewMode === 'settings';

  // Capabilities granted to the current user. The auth gate below already
  // guarantees at least one of reader/chat/admin once the app renders, but
  // hooks run before that gate, so this is computed unconditionally.
  const caps = auth.user?.capabilities ?? [];
  const canReader = caps.includes('reader');
  const canChat = caps.includes('chat');
  const canAdmin = caps.includes('admin');

  // Boot coercion (admin-console spec §3, generalized to all three views): a
  // persisted viewMode the user no longer (or never did) have the capability
  // for gets coerced to the first permitted view (reader, then chat, then
  // admin). Waits for the auth probe to resolve so a loading session isn't
  // bounced before /v1/auth/me answers.
  useViewModeGuard({ viewMode, setViewMode, authState: auth.state, caps });

  // Populate the optional project picker (Task 9) once the user is signed
  // in. Mirrors LibraryPage's loadProjects — same endpoint/shape, same
  // fail-soft-to-empty-list behavior so the picker just shows "No project"
  // options if this fetch fails rather than breaking the reader.
  useEffect(() => {
    if (auth.state !== 'active') return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/projects');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setProjects(data);
      } catch {
        if (!cancelled) setProjects([]);
      }
    })();
    return () => { cancelled = true; };
  }, [auth.state, apiHost, apiPort, projectsVersion]);

  const ttsEngine = useTtsEngine({
    textItems, currentSentenceIndex, setCurrentSentenceIndex,
    playbackIndexRef, currentPage, setCurrentPage, numPages,
    selectedVoice, playbackSpeed, isLocalhost, volume,
    apiHost, apiPort, requestTimeout, unlimitedBatchTimeout,
    backendAvailable, pdfFileName,
    setStatus, showToast,
    enabled: !inChat && !inAdmin && !inLibrary && !inSettings,
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
    ollamaHost, ollamaPort, inferenceSource, selectedModel,
    chatTtsMode, chatAutoTts, inference, onInferencePersist: setInference,
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
    inferenceBudget: chatInferenceBudget,
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
    pins: chatPins,
    addPin: chatAddPin,
    removePin: chatRemovePin,
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

  // Navigation helpers (used by PdfViewer, MobileBottomNav, keyboard shortcuts)
  const goToNextPage = useCallback(() => setCurrentPage(p => Math.min(numPages, p + 1)), [numPages, setCurrentPage]);
  const goToPrevPage = useCallback(() => setCurrentPage(p => Math.max(1, p - 1)), [setCurrentPage]);
  const skipToPrevSentence = useCallback(() => setCurrentSentenceIndex(prev => Math.max(-1, prev - 1)), [setCurrentSentenceIndex]);

  const checkBackend = async () => {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), requestTimeout * 1000);
      const response = await apiFetch(apiHost, apiPort, '/v1/health', {
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
    if (inChat || inAdmin || inLibrary || inSettings) return;
    e.preventDefault(); e.stopPropagation(); setIsDragging(true);
  };
  const handleDragLeave = (e) => {
    if (inChat || inAdmin || inLibrary || inSettings) return;
    e.preventDefault(); e.stopPropagation(); setIsDragging(false);
  };
  const handleDrop = (e) => {
    if (inChat || inAdmin || inLibrary || inSettings) return;
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
    chatAddPin(makePin({ doc_id: docId, fileName: pdfFileName, page, kind: 'page', text }));
    setViewMode('chat');
  }, [pdfFileName, textItems, ensureDocHash, setViewMode, showToast, chatAddPin]);

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
    chatAddPin(makePin({ doc_id: docId, fileName: pdfFileName, page: currentPage, kind: 'selection', text }));
    setViewMode('chat');
  }, [pdfFileName, currentPage, ensureDocHash, setViewMode, showToast, chatAddPin]);

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
  }, [stopPlayback, stopChatPlayback, closeDocument]);

  // ---------- WORKSPACE (folder open) ----------
  // Loads a workspace-relative file into the reader WITHOUT touching the
  // 5-doc library (synthesized docs are workspace-scoped). Reused for initial
  // open, link navigation, and Back/Forward.
  const onOpenDoc = useCallback(async (path, { anchor } = {}) => {
    if (!workspace) return;
    try {
      const text = await workspace.readText(path);
      const name = path.split('/').pop();
      if (isMarkdownPath(path)) loadMarkdownDocument(text, name);
      else loadTextDocument(text, name);
      saveWorkspaceState({ rootName: workspace.rootName, handle: workspace.handle || null, lastPath: path });
      if (anchor) {
        setTimeout(() => {
          const el = document.getElementById(anchor);
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 100);
      }
    } catch {
      showToast('Could not open that file from the folder.', 3000);
    }
  }, [workspace, loadMarkdownDocument, loadTextDocument, showToast]);

  const adoptWorkspace = useCallback((ws) => {
    const entry = pickEntryFile(ws.listFiles());
    if (!entry) {
      showToast('No Markdown files found in that folder.', 4000);
      return;
    }
    setWorkspace(ws);
    setWorkspaceEntryPath(entry);
    setViewMode('reader');
  }, [showToast, setViewMode]);

  const openFolder = useCallback(async () => {
    if (typeof window !== 'undefined' && window.showDirectoryPicker) {
      try {
        const handle = await window.showDirectoryPicker({ mode: 'read' });
        adoptWorkspace(await createFsaWorkspace(handle));
      } catch (e) {
        if (e?.name !== 'AbortError') showToast('Could not open folder.', 3000);
      }
    } else {
      folderInputRef.current?.click(); // webkitdirectory fallback
    }
  }, [adoptWorkspace, showToast]);

  const handleFolderInput = useCallback((e) => {
    const files = e.target.files;
    if (files && files.length) adoptWorkspace(createSnapshotWorkspace(files));
    e.target.value = '';
  }, [adoptWorkspace]);

  // Restore a saved workspace on load. If the FSA handle still has permission,
  // silently re-open it; otherwise surface a one-click reconnect affordance.
  useEffect(() => {
    if (!isLibLoaded) return;
    (async () => {
      try {
        const saved = await getWorkspaceState();
        if (!saved) return;
        if (saved.handle && saved.handle.queryPermission) {
          const perm = await saved.handle.queryPermission({ mode: 'read' });
          if (perm === 'granted') {
            const ws = await createFsaWorkspace(saved.handle);
            if (workspaceRef.current) return; // user opened a folder during the await
            setWorkspace(ws);
            setWorkspaceEntryPath(saved.lastPath || pickEntryFile(ws.listFiles()));
          } else {
            setReconnect({ rootName: saved.rootName }); // needs a user gesture
          }
        } else {
          setReconnect({ rootName: saved.rootName }); // snapshot: must re-pick
        }
      } catch (e) {
        // Don't crash mount — workspace restore is best-effort.
        console.warn('Workspace restore failed:', e);
      }
    })();
  }, [isLibLoaded]); // stable state setters don't need to be listed

  const reconnectFolder = useCallback(async () => {
    try {
      const saved = await getWorkspaceState();
      if (saved?.handle?.requestPermission) {
        const perm = await saved.handle.requestPermission({ mode: 'read' });
        if (perm === 'granted') {
          const ws = await createFsaWorkspace(saved.handle);
          if (workspaceRef.current) return; // user opened a folder during the await
          setWorkspace(ws);
          setWorkspaceEntryPath(saved.lastPath || pickEntryFile(ws.listFiles()));
          setReconnect(null);
          return;
        }
      }
      openFolder(); // snapshot or denied: full re-pick
    } catch (e) {
      console.warn('Reconnect failed:', e);
      openFolder();
    }
  }, [openFolder]);

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
        const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}`);
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
    if (!docId) { showToast('Could not read document bytes — re-open the file and try again.', 4000); return; }
    const setIndex = (id, patch) =>
      setDocIndexByDocId((prev) => ({ ...prev, [id]: { ...(prev[id] || {}), ...patch } }));

    // Re-index an indexed doc: the server re-extracts from its stored bytes.
    // Only the sole holder or an admin may — others get a notice saying why.
    // A server without the bytes (most docs indexed before A1: the browser
    // sent chunks, never the file) says bytes_missing; then upload the local
    // copy below instead — that restores the bytes and, for legacy content,
    // re-extracts from them.
    let reuploadForReindex = false;
    if (docIndexByDocId[docId]?.state === 'indexed') {
      const out = await requestReindex({ apiHost, apiPort, docId });
      if (out.notice) { showToast(out.notice, 6000); return; }
      if (out.started) {
        setIndex(docId, { state: 'extracting' });
        await pollIndexUntilSettled({ apiDocId: docId, stateKey: docId, apiHost, apiPort, setDocIndexByDocId, showToast });
        return;
      }
      reuploadForReindex = true;
    }

    setIndex(docId, { state: 'uploading' });
    let result;
    try {
      const record = await getBook(pdfFileName);
      if (!record?.data) throw new Error('File is not in the local library — re-open it and try again.');
      result = await registerDocument({
        apiHost, apiPort, file: new Blob([record.data]), fileName: pdfFileName,
        clientDocId: docId, projectId: docProjectId, tags: parseTagsInput(docTagsText),
      });
    } catch (e) {
      setIndex(docId, { state: 'failed' });
      showToast(`Indexing failed: ${e.message}`, 6000);
      return;
    }
    // The server's hash is authoritative for every API call (spec §8), but
    // IndexButton reads its state from docIndexByDocId[currentDocId], and
    // currentDocId is never repointed to the server's id — it stays the
    // local hash this flow started with. So progress is written under
    // `docId`, not `serverId`: writing under the server's id would leave the
    // button reading a key nothing updates if the two ever disagree.
    const serverId = result.docId;
    if (serverId !== docId) console.warn('Server doc id differs from local hash', { docId, serverId });
    setIndex(docId, { state: result.state });
    if (result.dedup && result.state === 'indexed' && !reuploadForReindex) {
      showToast('Already indexed — added to your library.', 4000);
      return;
    }
    await pollIndexUntilSettled({ apiDocId: serverId, stateKey: docId, apiHost, apiPort, setDocIndexByDocId, showToast });
  }, [pdfFileName, ensureDocHash, docIndexByDocId, showToast, apiHost, apiPort, docProjectId, docTagsText]);

  // ---------- DOCLING CONVERSION ----------
  // Mirror of handleIndexDocument: uploads (registers) the PDF bytes →
  // starts /convert → polls until conversion+indexing finish. Auto-switches
  // the reader to the MD view on success.
  const handleConvertDocument = useCallback(async (options) => {
    if (!pdfFileName || fileType !== 'pdf') return;
    const docId = await ensureDocHash();
    if (!docId) {
      showToast('Could not read document bytes — re-open the file and try again.', 4000);
      return;
    }
    setDocConvertByDocId((prev) => ({
      ...prev,
      [docId]: { ...(prev[docId] || {}), state: 'uploading', error: null },
    }));

    // 1. Register (upload) the document — but don't link the project yet.
    // Linking has to wait until after the convert kick-off below succeeds:
    // linking first would make the uploader's own convert fail with
    // in_project (a content-changing op on shared/in-project content is
    // refused — spec §5).
    let result;
    try {
      const record = await getBook(pdfFileName);
      if (!record?.data) throw new Error('File is not in the local library — re-open it and try again.');
      result = await registerDocument({
        apiHost, apiPort, file: new Blob([record.data], { type: 'application/pdf' }), fileName: pdfFileName,
        clientDocId: docId, tags: parseTagsInput(docTagsText), linkProject: false,
      });
    } catch (e) {
      console.error('Doc register failed:', e);
      setDocConvertByDocId((prev) => ({
        ...prev,
        [docId]: { ...(prev[docId] || {}), state: 'failed', error: e.message },
      }));
      showToast(`Convert failed: ${e.message}`, 5000);
      return;
    }
    // Same split as handleIndexDocument: convertDocId (the server's hash) is
    // authoritative for every API call below, but the convert button reads
    // docConvertByDocId[currentDocId] — the local hash — so all UI state
    // stays keyed by `docId`.
    const convertDocId = result.docId;
    if (convertDocId !== docId) console.warn('Server doc id differs from local hash', { docId, convertDocId });

    // 2. Kick off the conversion job.
    setDocConvertByDocId((prev) => ({
      ...prev,
      [docId]: { ...(prev[docId] || {}), state: 'converting', options, error: null },
    }));
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(convertDocId)}/convert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(options),
      });
      if (!res.ok) throw new Error(await describeRefusal(res));
    } catch (e) {
      console.error('Convert kick-off failed:', e);
      setDocConvertByDocId((prev) => ({
        ...prev,
        [docId]: { ...(prev[docId] || {}), state: 'failed', error: e.message },
      }));
      showToast(`Could not start conversion: ${e.message}`, 6000);
      return;
    }

    // Now that the kick-off succeeded, link the project (fail-soft — see linkDocToProject).
    if (docProjectId) linkDocToProject({ apiHost, apiPort, projectId: docProjectId, docId: convertDocId });

    showToast('Converting with Docling — this can take a few minutes.', 4000);

    // 3. Poll (server id for the network call, local hash for the UI keys —
    // see the comment above convertDocId).
    await pollConvertUntilSettled({
      apiDocId: convertDocId, stateKey: docId, apiHost, apiPort,
      setDocConvertByDocId, setDocIndexByDocId, setDocViewByDocId, showToast,
    });
  }, [pdfFileName, fileType, ensureDocHash, showToast, apiHost, apiPort, docProjectId, docTagsText]);

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
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(currentDocId)}/markdown`);
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

    try {
      await deleteConvertedMarkdown({ apiHost, apiPort, docId: currentDocId });
    } catch (e) {
      console.error('Markdown delete failed:', e);
      showToast(`Could not delete Markdown: ${e.message}`, 5000);
      return;
    }

    setDocConvertByDocId((prev) => ({
      ...prev,
      [currentDocId]: { ...(prev[currentDocId] || {}), state: 'idle', pageCount: 0, error: null, options: null },
    }));
    // The server falls back to its own extraction from the stored bytes and
    // re-indexes: follow that instead of showing the doc as never indexed.
    setDocIndexByDocId((prev) => ({
      ...prev,
      [currentDocId]: { state: 'extracting', chunkCount: 0, embeddedCount: 0 },
    }));
    // If the user was reading the MD view, drop back to the PDF rendering.
    setDocViewByDocId((prev) => ({ ...prev, [currentDocId]: 'pdf' }));
    showToast('Converted Markdown deleted — re-indexing from the file.', 3000);
    await pollIndexUntilSettled({
      apiDocId: currentDocId, stateKey: currentDocId, apiHost, apiPort, setDocIndexByDocId, showToast,
    });
  }, [currentDocId, apiHost, apiPort, showToast]);

  const currentConvertEntry = currentDocId ? docConvertByDocId[currentDocId] : null;
  const currentDocView = currentDocId ? docViewByDocId[currentDocId] || 'pdf' : 'pdf';
  const setCurrentDocView = useCallback((mode) => {
    if (!currentDocId) return;
    setDocViewByDocId((prev) => ({ ...prev, [currentDocId]: mode }));
  }, [currentDocId]);

  // --- AUTH GATE ---
  // Runs after all hooks (rules-of-hooks safe) and before every render branch,
  // so an unauthenticated visitor sees the login/pending/disabled screen rather
  // than the app or its loading spinner. With AUTH_ENABLED=false on a loopback
  // backend, /v1/auth/me returns the seed admin → state 'active' → app renders.
  // Also gate active users with no capabilities — they see NoAccessScreen.
  if (auth.state !== 'active' || !(auth.user?.capabilities?.length)) {
    return (
      <AuthGate
        state={auth.state}
        user={auth.user}
        onLogin={auth.login}
        onLogout={auth.logout}
        onRetry={auth.refresh}
      />
    );
  }

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
          isAdmin={canAdmin}
          canReader={canReader}
          canChat={canChat}
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
          workspaceName={workspace?.rootName}
          onCloseWorkspace={() => { setWorkspace(null); setWorkspaceEntryPath(null); clearWorkspaceState(); }}
          user={auth.user}
          onLogout={auth.logout}
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
          // Mount gate mirroring the admin console below: hiding via
          // ViewSwitcher is cosmetic, this is the render-time boundary that
          // covers the brief window before useViewModeGuard's effect fires.
          canChat && <ChatSidebar
            theme={theme}
            darkMode={darkMode}
            effectiveIsMobile={effectiveIsMobile}
            sidebarOpen={sidebarOpen}
            inferenceSource={inferenceSource}
            selectedModel={selectedModel} setSelectedModel={setSelectedModel}
            availableModels={availableModels}
            inferenceBudget={chatInferenceBudget}
            reachable={ollamaReachable}
            refreshModels={refreshModels}
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
        ) : inAdmin || inLibrary || inSettings ? null : (
        canReader && <Sidebar
          theme={theme}
          darkMode={darkMode}
          effectiveIsMobile={effectiveIsMobile}
          sidebarOpen={sidebarOpen}
          sidebarTab={sidebarTab} setSidebarTab={setSidebarTab}
          hasDocument={hasDocument}
          pdfDoc={pdfDoc}
          pdfOutline={pdfOutline}
          textItems={textItems}
          currentSentenceIndex={currentSentenceIndex}
          sentenceRefs={sentenceRefs}
          calculateReadingProgress={calculateReadingProgress}
          handleMobileSentenceClick={handleMobileSentenceClick}
          handleSentenceContextMenu={handleSentenceContextMenu}
          handleChapterNavigation={handleChapterNavigation}
        />
        ))}

        {inChat ? (
          // Mount gate mirroring the admin console below: hiding via
          // ViewSwitcher is cosmetic, this is the render-time boundary that
          // covers the brief window before useViewModeGuard's effect fires.
          canChat && <ChatView
            theme={theme}
            darkMode={darkMode}
            effectiveIsMobile={effectiveIsMobile}
            messages={chatMessages}
            isStreaming={chatIsStreaming}
            selectedModel={selectedModel}
            inferenceBudget={chatInferenceBudget}
            reachable={ollamaReachable}
            sendMessage={chatSendMessage}
            stopStream={chatStopStream}
            speakingMessageId={speakingMessageId}
            speakMessage={speakMessage}
            stopSpeaking={stopSpeaking}
            downloadingMessageId={downloadingMessageId}
            downloadMessageAudio={isLocalhost ? handleDownloadMessageAudio : null}
            showToast={showToast}
            pins={chatPins}
            onRemovePin={chatRemovePin}
            numCtx={inference.numCtx}
            draft={chatDraft}
            setDraft={setChatDraft}
            pendingAttachments={chatPendingAttachments}
            setPendingAttachments={setChatPendingAttachments}
          />
        ) : inAdmin ? (
          // Mount gate: the console renders nothing when the current user
          // isn't an admin — hiding is cosmetic, server rails are the
          // boundary, and the guard hook flips viewMode back to reader.
          auth.user?.role === 'admin' && (
            <AdminConsole
              theme={theme}
              apiHost={apiHost}
              apiPort={apiPort}
              currentUserId={auth.user.id}
              onBack={() => setViewMode('reader')}
              showToast={showToast}
            />
          )
        ) : inLibrary ? (
          <LibraryPage
            theme={theme}
            apiHost={apiHost}
            apiPort={apiPort}
            showToast={showToast}
            onProjectsChanged={() => setProjectsVersion((v) => v + 1)}
          />
        ) : inSettings ? (
          <SettingsPage
            theme={theme}
            voiceSettings={{ selectedVoice, setSelectedVoice, playbackSpeed, setPlaybackSpeed, volume, setVolume, isLocalhost, setIsLocalhost, requestTimeout, setRequestTimeout, unlimitedBatchTimeout, setUnlimitedBatchTimeout, isPreviewingVoice, previewVoice, stopVoicePreview, clearCache }}
            chatSettings={{ inferenceSource, setInferenceSource, chatTtsMode, setChatTtsMode, chatAutoTts, setChatAutoTts, inferenceByModel, setInferenceByModel, availableModels, selectedModel }}
            connectionSettings={{ apiHost, setApiHost, apiPort, setApiPort, ollamaHost, setOllamaHost, ollamaPort, setOllamaPort, inferenceSource, backendAvailable }}
            appearanceSettings={{ darkMode, setDarkMode, layoutMode, setLayoutMode, mobileBreakpoint, setMobileBreakpoint, showHeaderControlsOnMobile, setShowHeaderControlsOnMobile }}
            accountProps={{ apiHost, apiPort, user: auth.user }}
          />
        ) : (
        // Mount gate mirroring the admin console above: hiding via
        // ViewSwitcher is cosmetic, this is the render-time boundary that
        // covers the brief window before useViewModeGuard's effect fires.
        canReader && (
        <WorkspaceProvider workspace={workspace} initialPath={workspaceEntryPath} onOpenDoc={onOpenDoc} onMissing={(path) => showToast(`"${path}" isn't in this folder`, 3000)}>
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
            projects={projects}
            docProjectId={docProjectId}
            setDocProjectId={setDocProjectId}
            docTagsText={docTagsText}
            setDocTagsText={setDocTagsText}
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
            openFolder={openFolder}
          />
          {reconnect && !workspace && (
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 px-4 py-2 rounded-lg shadow-lg bg-white/90 dark:bg-slate-800/90 border border-slate-200 dark:border-slate-700">
              <button onClick={reconnectFolder} className="text-sm underline text-blue-500">
                Reconnect folder &ldquo;{reconnect.rootName}&rdquo;
              </button>
            </div>
          )}
        </WorkspaceProvider>
        )
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
          hasDocument={hasDocument && !inChat && !inAdmin && !inLibrary && !inSettings}
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

      {/* Floating control bar — only visible in distraction-free mode.
          Carries prev/play/next + page indicator + Exit when reading; just
          Exit in chat mode or with no doc open. F also toggles. */}
      {distractionFree && (
        <DistractionFreeBar
          onExit={() => setDistractionFree(false)}
          hasDocument={hasDocument}
          inChat={inChat}
          isPlaying={isPlaying}
          handlePlayPause={handlePlayPause}
          skipToNextSentence={skipToNextSentence}
          skipToPrevSentence={skipToPrevSentence}
          currentPage={currentPage}
          numPages={numPages}
          setCurrentPage={setCurrentPage}
        />
      )}

      {/* Hidden folder input — webkitdirectory fallback when showDirectoryPicker is unavailable */}
      <input
        ref={folderInputRef}
        type="file"
        webkitdirectory=""
        directory=""
        multiple
        style={{ display: 'none' }}
        onChange={handleFolderInput}
      />
    </div>
  );
}