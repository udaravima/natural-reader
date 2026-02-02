import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Play, Pause, Square, Upload, ChevronLeft, ChevronRight,
  Volume2, SkipForward, SkipBack, Zap, Loader2, Moon, Sun,
  ZoomIn, ZoomOut, Keyboard, Clock, VolumeX, Volume1,
  Maximize, Minimize, RotateCcw, Download, BookOpen, List, Trash2, Library,
  PlayCircle, MousePointer2, X, Menu, PanelLeftClose, Settings
} from 'lucide-react';
import * as pdfjsLib from 'pdfjs-dist';
import { saveBook, getBook, getRecentBooks, deleteBook, updateBookMeta } from './db';

// Configure PDF.js worker for offline use
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url
).toString();

// Default voices available in Kokoro - each with a sample sentence showcasing their character
const KOKORO_VOICES = [
  { id: 'af_heart', name: 'Heart (US Female)', sampleText: "Hi, I'm Heart! My warm and caring voice makes every story feel like a heartfelt conversation." },
  { id: 'af_bella', name: 'Bella (US Female)', sampleText: "Hello, I'm Bella! My elegant and refined tone brings sophistication to your reading experience." },
  { id: 'af_alloy', name: 'Alloy (US Female)', sampleText: "Hey there, I'm Alloy! My versatile voice adapts smoothly to any content you throw my way." },
  { id: 'af_aoede', name: 'Aoede (US Female)', sampleText: "Greetings, I'm Aoede! Named after the muse, I bring artistic expression to every word I speak." },
  { id: 'af_jessica', name: 'Jessica (US Female)', sampleText: "Hi, I'm Jessica! My friendly and approachable voice makes complex topics feel easy to understand." },
  { id: 'af_kore', name: 'Kore (US Female)', sampleText: "Hello, I'm Kore! My youthful energy brings freshness and vitality to every sentence." },
  { id: 'af_nicole', name: 'Nicole (US Female)', sampleText: "Hi there, I'm Nicole! My clear and professional tone is perfect for focused reading sessions." },
  { id: 'af_nova', name: 'Nova (US Female)', sampleText: "Hey, I'm Nova! My bright and dynamic voice illuminates every chapter like a star." },
  { id: 'af_river', name: 'River (US Male)', sampleText: "Hello, I'm River! My calm and flowing voice carries you smoothly through any narrative." },
  { id: 'af_sarah', name: 'Sarah (US Female)', sampleText: "Hi, I'm Sarah! My natural and sincere voice makes every reading feel authentic and genuine." },
  { id: 'af_sky', name: 'Sky (US Female)', sampleText: "Hey there, I'm Sky! My light and airy voice lifts your imagination to new heights." },
  { id: 'am_michael', name: 'Michael (US Male)', sampleText: "Hi, I'm Michael! My confident and articulate voice commands attention with every word." },
  { id: 'am_adam', name: 'Adam (US Male)', sampleText: "Hello, I'm Adam! My deep and trustworthy voice provides a grounded listening experience." },
  { id: 'am_echo', name: 'Echo (US Male)', sampleText: "Hey, I'm Echo! My resonant voice leaves a lasting impression on everything I narrate." },
  { id: 'am_eric', name: 'Eric (US Male)', sampleText: "Hi there, I'm Eric! My engaging and personable tone keeps listeners captivated throughout." },
  { id: 'am_fenrir', name: 'Fenrir (US Male)', sampleText: "Greetings, I'm Fenrir! My powerful and bold voice brings intensity to dramatic passages." },
  { id: 'am_liam', name: 'Liam (US Male)', sampleText: "Hello, I'm Liam! My warm and relatable voice feels like a trusted companion reading by your side." },
  { id: 'am_onyx', name: 'Onyx (US Male)', sampleText: "Hi, I'm Onyx! My rich and velvety voice adds depth and elegance to any text." },
  { id: 'am_puck', name: 'Puck (US Male)', sampleText: "Hey, I'm Puck! My playful and mischievous tone brings whimsy to every story I tell." },
  { id: 'bf_emma', name: 'Emma (UK Female)', sampleText: "Hello, I'm Emma! My refined British accent adds a touch of classic elegance to your reading." },
  { id: 'bf_alice', name: 'Alice (UK Female)', sampleText: "Hi there, I'm Alice! My gentle British voice guides you through stories with poise and grace." },
  { id: 'bf_isabella', name: 'Isabella (UK Female)', sampleText: "Greetings, I'm Isabella! My sophisticated British tone brings timeless charm to every narrative." },
  { id: 'bf_lily', name: 'Lily (UK Female)', sampleText: "Hello, I'm Lily! My sweet and melodic British voice blooms beautifully in every passage." },
  { id: 'bm_daniel', name: 'Daniel (UK Male)', sampleText: "Hi, I'm Daniel! My polished British accent delivers clarity and distinction to your content." },
  { id: 'bm_fable', name: 'Fable (UK Male)', sampleText: "Hello, I'm Fable! My storytelling British voice was born to bring tales to life magically." },
  { id: 'bm_george', name: 'George (UK Male)', sampleText: "Greetings, I'm George! My authoritative British voice lends gravitas to important passages." },
  { id: 'bm_lewis', name: 'Lewis (UK Male)', sampleText: "Hi there, I'm Lewis! My thoughtful British tone contemplates every word with care and precision." },
];

// Keyboard shortcuts config
const SHORTCUTS = {
  PLAY_PAUSE: ' ',
  STOP: 'Escape',
  PREV_SENTENCE: 'ArrowLeft',
  NEXT_SENTENCE: 'ArrowRight',
  PREV_PAGE: 'PageUp',
  NEXT_PAGE: 'PageDown',
  ZOOM_IN: '+',
  ZOOM_OUT: '-',
  TOGGLE_DARK: 'd',
};

export default function App() {
  // Helper to safely get localStorage values
  const getStoredValue = (key, defaultValue) => {
    try {
      const stored = localStorage.getItem(`neural-pdf-${key}`);
      if (stored !== null) {
        return JSON.parse(stored);
      }
    } catch (e) {
      console.warn(`Failed to load ${key} from localStorage`, e);
    }
    return defaultValue;
  };

  const [pdfDoc, setPdfDoc] = useState(null);
  const [pdfFileName, setPdfFileName] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(() => getStoredValue('scale', 1.2));
  const [textItems, setTextItems] = useState([]);
  const [isLibLoaded, setIsLibLoaded] = useState(false);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentSentenceIndex, setCurrentSentenceIndex] = useState(-1);
  const [playbackSpeed, setPlaybackSpeed] = useState(() => getStoredValue('playbackSpeed', 1.0));
  const [selectedVoice, setSelectedVoice] = useState(() => getStoredValue('selectedVoice', 'af_heart'));
  const [isLocalhost, setIsLocalhost] = useState(() => getStoredValue('isLocalhost', true));
  const [apiHost, setApiHost] = useState(() => getStoredValue('apiHost', 'localhost'));
  const [apiPort, setApiPort] = useState(() => getStoredValue('apiPort', '8000'));
  const [status, setStatus] = useState('Initializing PDF Engine...');

  // Enhanced Features State
  const [darkMode, setDarkMode] = useState(() => getStoredValue('darkMode', false));
  const [volume, setVolume] = useState(() => getStoredValue('volume', 1.0));
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [readingStartTime, setReadingStartTime] = useState(null);
  const [totalWordsRead, setTotalWordsRead] = useState(0);
  const [fitMode, setFitMode] = useState('custom');
  const [isDragging, setIsDragging] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [pdfOutline, setPdfOutline] = useState([]);
  const [sidebarTab, setSidebarTab] = useState('sentences'); // 'sentences' or 'chapters'
  const [backendAvailable, setBackendAvailable] = useState(null); // null = checking, true/false
  const [toastMessage, setToastMessage] = useState(null);
  const [recentBooks, setRecentBooks] = useState([]);
  const [currentFileRef, setCurrentFileRef] = useState(null); // Store current file for saving
  const [contextMenu, setContextMenu] = useState(null); // {x, y, sentenceIndex}
  const [selectedText, setSelectedText] = useState(''); // For selective read
  const [isReadingSelection, setIsReadingSelection] = useState(false);
  const [isPreviewingVoice, setIsPreviewingVoice] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [isMobile, setIsMobile] = useState(false);

  // Mobile Configuration
  const [mobileBreakpoint, setMobileBreakpoint] = useState(() => getStoredValue('mobileBreakpoint', 768));
  const [layoutMode, setLayoutMode] = useState(() => getStoredValue('layoutMode', 'auto')); // 'auto', 'desktop', 'mobile'
  const [showHeaderControlsOnMobile, setShowHeaderControlsOnMobile] = useState(() => getStoredValue('showHeaderControlsOnMobile', false));
  const voicePreviewRef = useRef(new Audio()); // Separate audio for voice preview
  const pdfContainerRef = useRef(null);

  // Buffer Management
  const audioCache = useRef(new Map());
  const audioRef = useRef(new Audio());
  const canvasRef = useRef(null);
  const textLayerRef = useRef(null); // For PDF text selection
  const fileInputRef = useRef(null);
  const pdfjsLibRef = useRef(null);
  const sentenceRefs = useRef([]);
  const sidebarRef = useRef(null);
  const playbackIndexRef = useRef(-1);

  // --- SETTINGS PERSISTENCE ---
  // Save settings to localStorage when they change
  useEffect(() => {
    localStorage.setItem('neural-pdf-darkMode', JSON.stringify(darkMode));
  }, [darkMode]);

  useEffect(() => {
    localStorage.setItem('neural-pdf-volume', JSON.stringify(volume));
  }, [volume]);

  useEffect(() => {
    localStorage.setItem('neural-pdf-scale', JSON.stringify(scale));
  }, [scale]);

  useEffect(() => {
    localStorage.setItem('neural-pdf-playbackSpeed', JSON.stringify(playbackSpeed));
  }, [playbackSpeed]);

  useEffect(() => {
    localStorage.setItem('neural-pdf-selectedVoice', JSON.stringify(selectedVoice));
  }, [selectedVoice]);

  useEffect(() => {
    localStorage.setItem('neural-pdf-isLocalhost', JSON.stringify(isLocalhost));
  }, [isLocalhost]);

  useEffect(() => {
    localStorage.setItem('neural-pdf-apiHost', JSON.stringify(apiHost));
  }, [apiHost]);

  useEffect(() => {
    localStorage.setItem('neural-pdf-apiPort', JSON.stringify(apiPort));
  }, [apiPort]);

  // Helper to build API URL
  const getApiUrl = (endpoint) => `http://${apiHost}:${apiPort}${endpoint}`;

  // --- READING PROGRESS PERSISTENCE ---
  // Save reading progress for the current PDF
  useEffect(() => {
    if (pdfFileName && currentPage > 0) {
      const progress = {
        page: currentPage,
        sentenceIndex: currentSentenceIndex,
        timestamp: Date.now(),
      };
      localStorage.setItem(`neural-pdf-progress-${pdfFileName}`, JSON.stringify(progress));
    }
  }, [pdfFileName, currentPage, currentSentenceIndex]);

  // Load reading progress when opening a PDF
  const loadReadingProgress = (fileName) => {
    try {
      const stored = localStorage.getItem(`neural-pdf-progress-${fileName}`);
      if (stored) {
        const progress = JSON.parse(stored);
        // Only restore if less than 7 days old
        if (Date.now() - progress.timestamp < 7 * 24 * 60 * 60 * 1000) {
          return progress;
        }
      }
    } catch (e) {
      console.warn('Failed to load reading progress', e);
    }
    return null;
  };

  // --- VOLUME CONTROL ---
  useEffect(() => {
    audioRef.current.volume = volume;
  }, [volume]);

  // --- MOBILE DETECTION & RESPONSIVE BEHAVIOR ---
  const wasMobileRef = useRef(false);

  useEffect(() => {
    const checkMobile = () => {
      const mobile = window.innerWidth < 768;
      const wasMobile = wasMobileRef.current;

      // Transitioning to mobile: close sidebar
      if (mobile && !wasMobile) {
        setSidebarOpen(false);
      }
      // Transitioning to desktop: open sidebar
      else if (!mobile && wasMobile) {
        setSidebarOpen(true);
      }

      wasMobileRef.current = mobile;
      setIsMobile(mobile);
    };

    // Initial check
    const initialMobile = window.innerWidth < 768;
    wasMobileRef.current = initialMobile;
    setIsMobile(initialMobile);
    setSidebarOpen(!initialMobile); // Start closed on mobile, open on desktop

    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Close sidebar when selecting a sentence on mobile
  const handleMobileSentenceClick = (index) => {
    setCurrentSentenceIndex(index - 1);
    setIsPlaying(true);
    if (isMobile) {
      setSidebarOpen(false);
    }
  };

  // --- KEYBOARD SHORTCUTS ---
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Don't trigger shortcuts when typing in inputs
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

      switch (e.key) {
        case SHORTCUTS.PLAY_PAUSE:
          e.preventDefault();
          handlePlayPause();
          break;
        case SHORTCUTS.STOP:
          stopPlayback();
          break;
        case SHORTCUTS.PREV_SENTENCE:
          if (e.shiftKey) {
            setCurrentSentenceIndex(prev => Math.max(-1, prev - 1));
          }
          break;
        case SHORTCUTS.NEXT_SENTENCE:
          if (e.shiftKey) {
            skipToNextSentence();
          }
          break;
        case SHORTCUTS.PREV_PAGE:
          setCurrentPage(p => Math.max(1, p - 1));
          break;
        case SHORTCUTS.NEXT_PAGE:
          setCurrentPage(p => Math.min(numPages, p + 1));
          break;
        case SHORTCUTS.ZOOM_IN:
          if (e.ctrlKey || e.metaKey) {
            e.preventDefault();
            setScale(s => Math.min(3, s + 0.2));
          }
          break;
        case SHORTCUTS.ZOOM_OUT:
          if (e.ctrlKey || e.metaKey) {
            e.preventDefault();
            setScale(s => Math.max(0.5, s - 0.2));
          }
          break;
        case SHORTCUTS.TOGGLE_DARK:
          if (e.ctrlKey || e.metaKey) {
            e.preventDefault();
            setDarkMode(d => !d);
          }
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [numPages, isPlaying]);

  // --- AUTO-SCROLL TO CURRENT SENTENCE ---
  useEffect(() => {
    if (currentSentenceIndex >= 0 && sentenceRefs.current[currentSentenceIndex]) {
      sentenceRefs.current[currentSentenceIndex].scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    }
  }, [currentSentenceIndex]);

  // --- ENGINE INITIALIZATION ---
  useEffect(() => {
    // PDF.js is now imported locally - no CDN needed
    pdfjsLibRef.current = pdfjsLib;
    setIsLibLoaded(true);
    setStatus('Ready to Open PDF');

    // Check backend health
    const checkBackend = async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);

        const response = await fetch(getApiUrl('/v1/synthesize'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: 'test', voice: 'af_heart', speed: 1.0 }),
          signal: controller.signal
        });

        clearTimeout(timeoutId);

        if (response.ok) {
          setBackendAvailable(true);
        } else {
          throw new Error('Backend error');
        }
      } catch (e) {
        console.warn('Backend not available:', e.message);
        setBackendAvailable(false);
        setIsLocalhost(false); // Auto-switch to system voice
        setToastMessage('Kokoro backend not detected. Using browser voice.');

        // Auto-dismiss toast after 5 seconds
        setTimeout(() => setToastMessage(null), 5000);
      }
    };

    checkBackend();

    // Load recent books from IndexedDB
    const loadRecentBooks = async () => {
      const books = await getRecentBooks();
      setRecentBooks(books);
    };
    loadRecentBooks();
  }, []);

  // --- PDF LOGIC ---
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

        // Manual text layer rendering (works across PDF.js versions)
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
      clearCache();
      setStatus(`Page ${pageNum} Ready`);
    } catch (err) {
      console.error(err);
      setStatus("Render Error");
    }
  };

  useEffect(() => {
    if (pdfDoc) renderPage(currentPage, pdfDoc);
  }, [pdfDoc, currentPage, scale]);

  // Process a PDF file (used by both file input and drag-and-drop)
  const processFile = (file) => {
    if (file?.type === 'application/pdf' && isLibLoaded) {
      const fileName = file.name;
      setCurrentFileRef(file); // Store reference for saving

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
            // Restore sentence index after a short delay (wait for page to render)
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

          setTotalWordsRead(0);
          setReadingStartTime(null);

          // Save to IndexedDB for library persistence
          saveBook(file, { page: 1, sentenceIndex: -1 }).then(() => {
            // Refresh recent books list
            getRecentBooks().then(setRecentBooks);
          });

          // Fetch PDF outline (Table of Contents)
          try {
            const outline = await doc.getOutline();
            if (outline && outline.length > 0) {
              setPdfOutline(outline);
              setSidebarTab('chapters'); // Auto-switch to chapters if available
            } else {
              setPdfOutline([]);
            }
          } catch (e) {
            console.warn('Could not load outline:', e);
            setPdfOutline([]);
          }
        } catch (err) {
          setStatus("Error loading PDF");
        }
      };
      reader.readAsArrayBuffer(file);
    }
  };

  // Open a book from the library (IndexedDB)
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

      // Restore reading position
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

      // Update last opened time
      updateBookMeta(fileName, {}).then(() => {
        getRecentBooks().then(setRecentBooks);
      });

      // Fetch outline
      try {
        const outline = await doc.getOutline();
        if (outline && outline.length > 0) {
          setPdfOutline(outline);
          setSidebarTab('chapters');
        } else {
          setPdfOutline([]);
        }
      } catch (e) {
        setPdfOutline([]);
      }
    } catch (e) {
      console.error("Failed to open from library:", e);
      setStatus("Failed to load book");
    }
  };

  // Delete a book from library
  const removeFromLibrary = async (fileName, e) => {
    e.stopPropagation(); // Don't trigger open
    await deleteBook(fileName);
    const books = await getRecentBooks();
    setRecentBooks(books);
    setToastMessage(`Removed "${fileName}" from library`);
    setTimeout(() => setToastMessage(null), 3000);
  };

  // Context menu for sentences
  const handleSentenceContextMenu = (e, sentenceIndex) => {
    e.preventDefault();
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      sentenceIndex
    });
  };

  // Close context menu when clicking elsewhere
  useEffect(() => {
    const handleClick = () => setContextMenu(null);
    if (contextMenu) {
      window.addEventListener('click', handleClick);
      return () => window.removeEventListener('click', handleClick);
    }
  }, [contextMenu]);

  // Continue reading from a specific sentence
  const continueFromHere = (sentenceIndex) => {
    setCurrentSentenceIndex(sentenceIndex - 1); // Will advance to sentenceIndex on play
    playbackIndexRef.current = sentenceIndex - 1;
    setIsPlaying(true);
    setContextMenu(null);
    setStatus(`Starting from sentence ${sentenceIndex + 1}`);
  };

  // Read only selected text (not sequential)
  const readSelection = async () => {
    const selection = window.getSelection().toString().trim();
    if (!selection) {
      setToastMessage("Select some text first");
      setTimeout(() => setToastMessage(null), 3000);
      return;
    }

    setSelectedText(selection);
    setIsReadingSelection(true);
    setStatus("Reading selection...");

    if (isLocalhost) {
      try {
        const response = await fetch(getApiUrl('/v1/synthesize'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: selection,
            voice: selectedVoice,
            speed: playbackSpeed
          })
        });

        if (!response.ok) throw new Error("TTS failed");
        const data = await response.json();
        const b64 = data.audio_base64;
        const blob = await (await fetch(`data:audio/wav;base64,${b64}`)).blob();
        const url = URL.createObjectURL(blob);

        audioRef.current.src = url;
        audioRef.current.onended = () => {
          setIsReadingSelection(false);
          setSelectedText('');
          setStatus("Selection read complete");
          URL.revokeObjectURL(url);
        };
        audioRef.current.play();
      } catch (e) {
        console.error("Selection read error:", e);
        setIsReadingSelection(false);
        setStatus("Failed to read selection");
      }
    } else {
      // Browser TTS fallback
      const utterance = new SpeechSynthesisUtterance(selection);
      utterance.rate = playbackSpeed;
      utterance.onend = () => {
        setIsReadingSelection(false);
        setSelectedText('');
        setStatus("Selection read complete");
      };
      window.speechSynthesis.speak(utterance);
    }
  };

  // Stop reading selection
  const stopSelectionRead = () => {
    audioRef.current.pause();
    window.speechSynthesis.cancel();
    setIsReadingSelection(false);
    setSelectedText('');
    setStatus("Selection stopped");
  };

  // Preview a voice's sample sentence
  const previewVoice = async (voiceId) => {
    const voice = KOKORO_VOICES.find(v => v.id === voiceId);
    if (!voice || !voice.sampleText) return;

    // Stop any existing preview
    voicePreviewRef.current.pause();
    window.speechSynthesis.cancel();
    setIsPreviewingVoice(true);
    setStatus(`Previewing ${voice.name}...`);

    if (isLocalhost && backendAvailable) {
      try {
        const response = await fetch(getApiUrl('/v1/synthesize'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: voice.sampleText,
            voice: voiceId,
            speed: playbackSpeed
          })
        });

        if (!response.ok) throw new Error("TTS failed");
        const data = await response.json();
        const b64 = data.audio_base64;
        const blob = await (await fetch(`data:audio/wav;base64,${b64}`)).blob();
        const url = URL.createObjectURL(blob);

        voicePreviewRef.current.src = url;
        voicePreviewRef.current.volume = volume;
        voicePreviewRef.current.onended = () => {
          setIsPreviewingVoice(false);
          setStatus("Preview complete");
          URL.revokeObjectURL(url);
        };
        voicePreviewRef.current.play();
      } catch (e) {
        console.error("Voice preview error:", e);
        setIsPreviewingVoice(false);
        setStatus("Preview failed");
      }
    } else {
      // Browser TTS fallback
      const utterance = new SpeechSynthesisUtterance(voice.sampleText);
      utterance.rate = playbackSpeed;
      utterance.onend = () => {
        setIsPreviewingVoice(false);
        setStatus("Preview complete");
      };
      window.speechSynthesis.speak(utterance);
    }
  };

  // Stop voice preview
  const stopVoicePreview = () => {
    voicePreviewRef.current.pause();
    window.speechSynthesis.cancel();
    setIsPreviewingVoice(false);
    setStatus("Preview stopped");
  };

  const handleFileUpload = (e) => {
    const file = e.target.files[0];
    processFile(file);
  };

  // Drag-and-drop handlers
  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      if (file.type === 'application/pdf') {
        processFile(file);
      } else {
        setStatus("Please drop a PDF file");
      }
    }
  };

  // Download page audio
  const downloadPageAudio = async () => {
    if (!textItems.length || !isLocalhost) {
      setStatus(isLocalhost ? "No text to download" : "Download requires Kokoro backend");
      return;
    }

    setIsDownloading(true);
    setStatus("Generating audio for page...");

    try {
      const response = await fetch(getApiUrl('/v1/batch_synthesize'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sentences: textItems,
          voice: selectedVoice,
          speed: playbackSpeed
        })
      });

      if (!response.ok) throw new Error("Batch synthesis failed");

      const data = await response.json();
      const b64 = data.audio_base64;

      // Convert base64 to blob and download
      const byteCharacters = atob(b64);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      const blob = new Blob([byteArray], { type: 'audio/wav' });

      // Create download link
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${pdfFileName.replace('.pdf', '')}_page${currentPage}.wav`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      setStatus(`Downloaded page ${currentPage} audio (${Math.round(data.duration_seconds)}s)`);
    } catch (err) {
      console.error("Download error:", err);
      setStatus("Failed to generate audio");
    } finally {
      setIsDownloading(false);
    }
  };

  // --- TTS ENGINE & BUFFERING ---
  const clearCache = () => {
    audioCache.current.forEach(url => URL.revokeObjectURL(url));
    audioCache.current.clear();
  };

  const fetchAudio = async (index) => {
    if (index < 0 || index >= textItems.length) return null;
    if (audioCache.current.has(index)) return audioCache.current.get(index);

    try {
      const response = await fetch(getApiUrl('/v1/synthesize'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: textItems[index],
          voice: selectedVoice,
          speed: playbackSpeed
        })
      });

      if (!response.ok) throw new Error("TTS Fail");
      const data = await response.json();

      // Handle potential different response shapes (base64 directly or nested)
      const b64 = data.audio_base64 || data.audio;
      const blob = await (await fetch(`data:audio/wav;base64,${b64}`)).blob();
      const url = URL.createObjectURL(blob);
      audioCache.current.set(index, url);
      return url;
    } catch (err) {
      console.error("Inference Error:", err);
      return null;
    }
  };

  const prefetchBuffer = useCallback(async (currentIndex) => {
    if (!isLocalhost) return;
    // Look ahead 2 sentences
    for (let i = 1; i <= 2; i++) {
      const target = currentIndex + i;
      if (target < textItems.length && !audioCache.current.has(target)) {
        fetchAudio(target);
      }
    }
  }, [textItems, selectedVoice, playbackSpeed, isLocalhost]);

  const handlePlayPause = () => {
    if (isPlaying) {
      setIsPlaying(false);
      audioRef.current.pause();
      window.speechSynthesis.cancel();
    } else {
      setIsPlaying(true);
      if (!readingStartTime) {
        setReadingStartTime(Date.now());
      }
    }
  };

  const stopPlayback = () => {
    setIsPlaying(false);
    playbackIndexRef.current = -1;
    setCurrentSentenceIndex(-1);
    audioRef.current.pause();
    window.speechSynthesis.cancel();
    setStatus("Playback Stopped");
  };

  const skipToNextSentence = () => {
    if (currentSentenceIndex < textItems.length - 1) {
      audioRef.current.pause();
      window.speechSynthesis.cancel();
      setCurrentSentenceIndex(prev => prev + 1);
    }
  };

  // --- READING STATISTICS ---
  const calculateReadingProgress = () => {
    if (textItems.length === 0) return 0;
    return Math.round(((currentSentenceIndex + 1) / textItems.length) * 100);
  };

  const calculateEstimatedTimeRemaining = () => {
    if (textItems.length === 0 || currentSentenceIndex < 0) return null;

    const remainingSentences = textItems.slice(currentSentenceIndex + 1);
    const remainingWords = remainingSentences.reduce((acc, s) => acc + s.split(' ').length, 0);

    // Average reading speed: ~150 words per minute, adjusted by playback speed
    const wordsPerMinute = 150 * playbackSpeed;
    const minutesRemaining = remainingWords / wordsPerMinute;

    if (minutesRemaining < 1) return 'Less than 1 min';
    if (minutesRemaining < 60) return `~${Math.ceil(minutesRemaining)} min`;
    const hours = Math.floor(minutesRemaining / 60);
    const mins = Math.ceil(minutesRemaining % 60);
    return `~${hours}h ${mins}m`;
  };

  // Track words read
  useEffect(() => {
    if (currentSentenceIndex >= 0 && textItems[currentSentenceIndex]) {
      const words = textItems[currentSentenceIndex].split(' ').length;
      setTotalWordsRead(prev => prev + words);
    }
  }, [currentSentenceIndex]);

  // Sync ref with state when state changes externally (e.g., user clicks a sentence)
  useEffect(() => {
    playbackIndexRef.current = currentSentenceIndex;
  }, [currentSentenceIndex]);

  // MAIN PLAYBACK LOOP
  useEffect(() => {
    if (!isPlaying) return;

    let active = true;

    const playLoop = async () => {
      // Use ref to get current position (avoids stale closure)
      const nextIdx = playbackIndexRef.current + 1;

      if (nextIdx >= textItems.length) {
        if (currentPage < numPages) {
          setStatus("Changing Page...");
          setCurrentPage(p => p + 1);
          playbackIndexRef.current = -1;
          setCurrentSentenceIndex(-1);
        } else {
          stopPlayback();
          setStatus("End of Document");
        }
        return;
      }

      const textToRead = textItems[nextIdx];
      // Update both ref and state
      playbackIndexRef.current = nextIdx;
      setCurrentSentenceIndex(nextIdx);
      prefetchBuffer(nextIdx);

      if (isLocalhost) {
        setStatus("Generating Voice...");
        const url = await fetchAudio(nextIdx);
        if (!active) return;

        if (url) {
          setStatus("Reading...");
          audioRef.current.src = url;
          audioRef.current.onended = () => {
            if (active) playLoop();
          };
          audioRef.current.play().catch(e => {
            console.error("Audio block", e);
            setStatus("Wait for interaction...");
          });
        } else {
          setStatus("Connection Error - Retrying...");
          setTimeout(() => {
            if (active) playLoop();
          }, 2000);
        }
      } else {
        setStatus("Using System Voice...");
        const ut = new SpeechSynthesisUtterance(textToRead);
        ut.rate = playbackSpeed;
        ut.onend = () => {
          if (active) playLoop();
        };
        window.speechSynthesis.speak(ut);
      }
    };

    if (audioRef.current.paused && !window.speechSynthesis.speaking) {
      playLoop();
    }

    return () => { active = false; };
  }, [isPlaying, textItems, currentPage]);

  // --- THEME CLASSES ---
  const theme = {
    bg: darkMode ? 'bg-slate-900' : 'bg-slate-50',
    bgSecondary: darkMode ? 'bg-slate-800' : 'bg-white',
    bgTertiary: darkMode ? 'bg-slate-700' : 'bg-slate-100',
    text: darkMode ? 'text-slate-100' : 'text-slate-900',
    textSecondary: darkMode ? 'text-slate-400' : 'text-slate-500',
    textMuted: darkMode ? 'text-slate-500' : 'text-slate-400',
    border: darkMode ? 'border-slate-700' : 'border-slate-200',
    borderSecondary: darkMode ? 'border-slate-600' : 'border-slate-100',
    hover: darkMode ? 'hover:bg-slate-700' : 'hover:bg-white',
    selection: darkMode ? 'selection:bg-blue-900' : 'selection:bg-blue-200',
    canvasBg: darkMode ? 'bg-slate-800' : 'bg-white',
    viewportBg: darkMode ? 'bg-slate-950/50' : 'bg-slate-200/50',
  };

  // Volume icon based on level
  const VolumeIcon = volume === 0 ? VolumeX : volume < 0.5 ? Volume1 : Volume2;

  if (!isLibLoaded) {
    return (
      <div className={`h-screen w-full flex flex-col items-center justify-center ${theme.bg} gap-4`}>
        <Loader2 className="animate-spin text-blue-600" size={48} />
        <p className={`${theme.textSecondary} font-medium`}>Booting Neural Engine...</p>
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col h-screen ${theme.bg} ${theme.text} font-sans ${theme.selection} transition-colors duration-300`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >

      {/* DRAG OVERLAY */}
      {isDragging && (
        <div className="fixed inset-0 z-[100] bg-blue-600/20 backdrop-blur-sm flex items-center justify-center pointer-events-none">
          <div className="bg-white dark:bg-slate-800 rounded-2xl p-12 shadow-2xl border-4 border-dashed border-blue-500 flex flex-col items-center gap-4">
            <Upload size={64} className="text-blue-500 animate-bounce" />
            <p className="text-xl font-bold text-blue-600 dark:text-blue-400">Drop PDF to Open</p>
          </div>
        </div>
      )}

      {/* READING PROGRESS BAR */}
      {pdfDoc && (
        <div className="h-1 bg-slate-300/20 w-full fixed top-0 left-0 z-50">
          <div
            className="h-full bg-gradient-to-r from-blue-500 via-cyan-500 to-teal-500 transition-all duration-500 ease-out"
            style={{ width: `${calculateReadingProgress()}%` }}
          />
        </div>
      )}

      {/* TOAST NOTIFICATION */}
      {toastMessage && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[200] animate-pulse">
          <div className={`px-6 py-3 rounded-xl shadow-2xl border ${darkMode ? 'bg-amber-900/90 border-amber-700 text-amber-100' : 'bg-amber-50 border-amber-300 text-amber-800'} flex items-center gap-3`}>
            <Zap size={18} className="text-amber-500" />
            <span className="font-medium text-sm">{toastMessage}</span>
            <button
              onClick={() => setToastMessage(null)}
              className="ml-2 opacity-60 hover:opacity-100 transition-opacity"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* CONTEXT MENU - Continue From Here */}
      {contextMenu && (
        <div
          className={`fixed z-[300] ${theme.bgSecondary} rounded-xl shadow-2xl border ${theme.border} py-2 min-w-[180px]`}
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            onClick={() => continueFromHere(contextMenu.sentenceIndex)}
            className={`w-full px-4 py-2.5 text-left text-sm flex items-center gap-3 ${theme.hover} ${theme.text} hover:text-blue-500`}
          >
            <PlayCircle size={16} className="text-blue-500" />
            Continue from here
          </button>
          <button
            onClick={() => {
              const text = textItems[contextMenu.sentenceIndex];
              if (text) {
                window.getSelection().removeAllRanges();
                navigator.clipboard.writeText(text);
                setToastMessage("Copied to clipboard");
                setTimeout(() => setToastMessage(null), 2000);
              }
              setContextMenu(null);
            }}
            className={`w-full px-4 py-2.5 text-left text-sm flex items-center gap-3 ${theme.hover} ${theme.text} hover:text-blue-500`}
          >
            <MousePointer2 size={16} className="text-slate-500" />
            Copy sentence
          </button>
        </div>
      )}

      {/* READ SELECTION FLOATING BUTTON */}
      {pdfDoc && !isReadingSelection && (
        <button
          onClick={readSelection}
          className={`fixed bottom-6 right-6 z-[150] px-4 py-3 rounded-2xl shadow-xl flex items-center gap-2 transition-all hover:scale-105 ${darkMode ? 'bg-gradient-to-r from-blue-600 to-cyan-600 text-white' : 'bg-gradient-to-r from-blue-500 to-cyan-500 text-white'}`}
          title="Select text and click to read it aloud"
        >
          <MousePointer2 size={18} />
          <span className="text-sm font-bold">Read Selection</span>
        </button>
      )}

      {/* READING SELECTION INDICATOR */}
      {isReadingSelection && (
        <div className="fixed bottom-6 right-6 z-[150] flex items-center gap-3">
          <div className={`px-4 py-3 rounded-2xl shadow-xl flex items-center gap-2 ${darkMode ? 'bg-green-700' : 'bg-green-500'} text-white`}>
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm font-bold">Reading...</span>
          </div>
          <button
            onClick={stopSelectionRead}
            className={`p-3 rounded-full shadow-xl ${darkMode ? 'bg-red-700' : 'bg-red-500'} text-white hover:scale-105 transition-all`}
            title="Stop reading"
          >
            <Square size={16} />
          </button>
        </div>
      )}

      {/* HEADER / CONTROL BAR */}
      <header className={`h-16 ${theme.bgSecondary} border-b ${theme.border} px-4 md:px-6 flex items-center justify-between z-20 sticky top-0 shadow-sm transition-colors duration-300`}>
        <div className="flex items-center gap-2 md:gap-3">
          {/* Sidebar Toggle Button */}
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className={`p-2.5 ${theme.bgTertiary} rounded-xl ${theme.hover} transition-all ${theme.textSecondary} hover:text-blue-500`}
            title={sidebarOpen ? "Hide Sidebar" : "Show Sidebar"}
          >
            {sidebarOpen ? <PanelLeftClose size={20} /> : <Menu size={20} />}
          </button>
          <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-cyan-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-blue-500/30">
            <Volume2 size={22} />
          </div>
          <div className="hidden sm:block">
            <h1 className={`text-sm font-bold uppercase tracking-tighter ${theme.textSecondary}`}>Neural PDF</h1>
            <p className="text-[10px] font-bold text-blue-500 truncate max-w-[200px]">{status}</p>
          </div>
        </div>

        {/* PLAYBACK CONTROLS - Hidden on mobile, shown on tablet+ */}
        <div className={`hidden md:flex items-center gap-2 ${theme.bgTertiary} p-1 rounded-xl border ${theme.border} shadow-inner`}>
          <button
            onClick={() => setCurrentSentenceIndex(prev => Math.max(-1, prev - 2))}
            className={`p-2 ${theme.hover} rounded-lg transition-colors ${theme.textSecondary}`}
            title="Previous (Shift+←)"
          >
            <SkipBack size={18} />
          </button>
          <button
            onClick={handlePlayPause}
            className={`px-6 py-2 rounded-lg font-bold flex items-center gap-2 transition-all min-w-[120px] justify-center ${isPlaying
              ? `${theme.bgSecondary} text-amber-500 shadow-sm`
              : 'bg-blue-600 text-white shadow-md hover:bg-blue-700 hover:shadow-lg'
              }`}
            title="Play/Pause (Space)"
          >
            {isPlaying ? <><Pause size={18} fill="currentColor" /> Pause</> : <><Play size={18} fill="currentColor" /> Read</>}
          </button>
          <button
            onClick={skipToNextSentence}
            className={`p-2 ${theme.hover} rounded-lg transition-colors ${theme.textSecondary}`}
            title="Next (Shift+→)"
          >
            <SkipForward size={18} />
          </button>
          <div className={`w-px h-6 ${theme.border} mx-1`}></div>
          <button
            onClick={stopPlayback}
            className={`p-2 ${theme.hover} ${theme.textMuted} hover:text-red-500 rounded-lg`}
            title="Stop (Esc)"
          >
            <Square size={16} fill="currentColor" />
          </button>
        </div>

        {/* RIGHT CONTROLS */}
        <div className="flex items-center gap-1 md:gap-3">
          {/* Estimated Time */}
          {pdfDoc && calculateEstimatedTimeRemaining() && (
            <div className={`hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-lg border ${theme.border} text-[10px] font-bold ${theme.textSecondary}`}>
              <Clock size={12} />
              {calculateEstimatedTimeRemaining()}
            </div>
          )}

          {/* TTS Mode Toggle - Hidden on mobile */}
          <div className={`hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg border text-[10px] font-black tracking-widest transition-colors ${isLocalhost
            ? 'bg-green-500/10 border-green-500/30 text-green-500'
            : `${theme.bgTertiary} ${theme.border} ${theme.textSecondary}`
            }`}>
            <Zap size={12} fill={isLocalhost ? "currentColor" : "none"} />
            <button onClick={() => setIsLocalhost(!isLocalhost)}>
              {isLocalhost ? "KOKORO" : "SYSTEM"}
            </button>
          </div>

          {/* Dark Mode Toggle */}
          <button
            onClick={() => setDarkMode(!darkMode)}
            className={`p-2.5 ${theme.bgTertiary} rounded-xl ${theme.hover} transition-all ${theme.textSecondary} hover:text-amber-500`}
            title="Toggle Dark Mode (Ctrl+D)"
          >
            {darkMode ? <Sun size={20} /> : <Moon size={20} />}
          </button>

          {/* Download Page Audio - Hidden on mobile */}
          {pdfDoc && isLocalhost && (
            <button
              onClick={downloadPageAudio}
              disabled={isDownloading || textItems.length === 0}
              className={`hidden sm:block p-2.5 ${theme.bgTertiary} rounded-xl ${theme.hover} transition-all ${isDownloading ? 'opacity-50 cursor-wait' : theme.textSecondary + ' hover:text-green-500'}`}
              title="Download Page Audio"
            >
              {isDownloading ? <Loader2 size={20} className="animate-spin" /> : <Download size={20} />}
            </button>
          )}

          {/* Keyboard Shortcuts - Hidden on mobile */}
          <button
            onClick={() => setShowShortcuts(!showShortcuts)}
            className={`hidden sm:block p-2.5 ${theme.bgTertiary} rounded-xl ${theme.hover} transition-all ${theme.textSecondary} hover:text-blue-500`}
            title="Keyboard Shortcuts"
          >
            <Keyboard size={20} />
          </button>

          {/* Upload Button */}
          <button
            onClick={() => fileInputRef.current.click()}
            className="p-2.5 bg-gradient-to-r from-slate-700 to-slate-800 text-white rounded-xl hover:from-slate-600 hover:to-slate-700 transition-all shadow-md"
          >
            <Upload size={20} />
          </button>
          <input type="file" ref={fileInputRef} onChange={handleFileUpload} accept=".pdf" className="hidden" />
        </div>
      </header>

      {/* KEYBOARD SHORTCUTS MODAL */}
      {showShortcuts && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center"
          onClick={() => setShowShortcuts(false)}
        >
          <div
            className={`${theme.bgSecondary} rounded-2xl p-6 max-w-md w-full mx-4 shadow-2xl border ${theme.border}`}
            onClick={e => e.stopPropagation()}
          >
            <h3 className={`text-lg font-bold mb-4 ${theme.text}`}>⌨️ Keyboard Shortcuts</h3>
            <div className="space-y-3">
              {[
                ['Space', 'Play / Pause'],
                ['Escape', 'Stop playback'],
                ['Shift + ←', 'Previous sentence'],
                ['Shift + →', 'Next sentence'],
                ['Page Up', 'Previous page'],
                ['Page Down', 'Next page'],
                ['Ctrl + +', 'Zoom in'],
                ['Ctrl + -', 'Zoom out'],
                ['Ctrl + D', 'Toggle dark mode'],
              ].map(([key, action]) => (
                <div key={key} className={`flex justify-between items-center py-2 border-b ${theme.borderSecondary}`}>
                  <span className={theme.textSecondary}>{action}</span>
                  <kbd className={`px-3 py-1 ${theme.bgTertiary} rounded-lg text-sm font-mono font-bold ${theme.text}`}>
                    {key}
                  </kbd>
                </div>
              ))}
            </div>
            <button
              onClick={() => setShowShortcuts(false)}
              className="mt-6 w-full py-2 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 hover:shadow-lg transition-all"
            >
              Got it!
            </button>
          </div>
        </div>
      )}

      <main className="flex-1 flex overflow-hidden relative">

        {/* MOBILE OVERLAY */}
        {sidebarOpen && isMobile && (
          <div
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-30 md:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* SIDEBAR: NAVIGATION & SETTINGS */}
        <aside className={`
          ${isMobile
            ? `fixed inset-y-0 left-0 z-40 w-80 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`
            : `relative ${sidebarOpen ? 'w-80' : 'w-0'} overflow-hidden`
          }
          ${theme.bgSecondary} border-r ${theme.border} flex flex-col shadow-xl transition-all duration-300 ease-in-out
          ${isMobile && sidebarOpen ? 'pt-16' : ''}
        `}>
          <div className={`${isMobile ? '' : 'w-80'} flex flex-col h-full`}>
            <div className={`p-4 border-b ${theme.borderSecondary} ${darkMode ? 'bg-slate-800/50' : 'bg-slate-50/50'}`}>
              <h3 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest mb-3`}>Settings</h3>
              <div className="flex flex-col gap-3">
                {/* Voice Selection */}
                <div className="space-y-2">
                  <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>VOICE</span>
                  <div className="flex gap-2">
                    <select
                      value={selectedVoice}
                      onChange={(e) => { setSelectedVoice(e.target.value); clearCache(); }}
                      className={`flex-1 text-xs font-bold p-2.5 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors`}
                    >
                      {KOKORO_VOICES.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
                    </select>
                    <button
                      onClick={() => isPreviewingVoice ? stopVoicePreview() : previewVoice(selectedVoice)}
                      disabled={!backendAvailable && isLocalhost}
                      className={`px-3 py-2.5 rounded-lg border transition-all flex items-center justify-center ${isPreviewingVoice
                        ? 'bg-blue-600 text-white border-blue-600 animate-pulse'
                        : `${theme.border} ${theme.hover} ${theme.textSecondary} hover:text-blue-500 hover:border-blue-400`
                        } ${(!backendAvailable && isLocalhost) ? 'opacity-50 cursor-not-allowed' : ''}`}
                      title={isPreviewingVoice ? "Stop preview" : "Preview this voice"}
                    >
                      {isPreviewingVoice ? <Square size={14} /> : <PlayCircle size={14} />}
                    </button>
                  </div>
                  {/* Voice Sample Text */}
                  {(() => {
                    const currentVoice = KOKORO_VOICES.find(v => v.id === selectedVoice);
                    return currentVoice?.sampleText && (
                      <p className={`text-[10px] leading-relaxed ${theme.textMuted} italic px-1 py-2 rounded-lg ${theme.bgTertiary} border ${theme.border}`}>
                        "{currentVoice.sampleText}"
                      </p>
                    );
                  })()}
                </div>

                {/* Speed Selection */}
                <div className="space-y-1">
                  <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>SPEED</span>
                  <select
                    value={playbackSpeed}
                    onChange={(e) => { setPlaybackSpeed(parseFloat(e.target.value)); clearCache(); }}
                    className={`w-full text-xs font-bold p-2.5 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors`}
                  >
                    {[0.5, 0.75, 1.0, 1.25, 1.5, 2.0].map(s => <option key={s} value={s}>{s}x Speed</option>)}
                  </select>
                </div>

                {/* Volume Control */}
                <div className="space-y-1">
                  <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>VOLUME</span>
                  <div className={`flex items-center gap-3 p-2.5 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
                    <VolumeIcon size={16} className={theme.textSecondary} />
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={volume}
                      onChange={(e) => setVolume(parseFloat(e.target.value))}
                      className="flex-1 h-2 appearance-none bg-slate-300 dark:bg-slate-600 rounded-full cursor-pointer accent-blue-500"
                    />
                    <span className={`text-xs font-bold ${theme.textSecondary} w-8`}>{Math.round(volume * 100)}%</span>
                  </div>
                </div>

                {/* API Configuration */}
                <div className="space-y-2">
                  <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>VOICE API</span>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold ${theme.textMuted} w-10 shrink-0`}>Host</span>
                      <input
                        type="text"
                        value={apiHost}
                        onChange={(e) => { setApiHost(e.target.value); setBackendAvailable(null); }}
                        placeholder="localhost"
                        className={`flex-1 text-xs font-bold p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors min-w-0`}
                        title="API Host (e.g., localhost or 192.168.1.100)"
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold ${theme.textMuted} w-10 shrink-0`}>Port</span>
                      <input
                        type="text"
                        value={apiPort}
                        onChange={(e) => { setApiPort(e.target.value); setBackendAvailable(null); }}
                        placeholder="8000"
                        className={`flex-1 text-xs font-bold p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors min-w-0`}
                        title="API Port (default: 8000)"
                      />
                    </div>
                  </div>
                  <div className="flex items-center justify-between gap-2 mt-1">
                    <span className={`text-[10px] ${backendAvailable === null ? theme.textMuted : backendAvailable ? 'text-green-500' : 'text-red-400'}`}>
                      {backendAvailable === null ? '⏳ Checking...' : backendAvailable ? '✓ Connected' : '✗ Unavailable'}
                    </span>
                    <button
                      onClick={async () => {
                        setBackendAvailable(null);
                        setStatus('Checking API connection...');
                        try {
                          const controller = new AbortController();
                          const timeoutId = setTimeout(() => controller.abort(), 3000);
                          const response = await fetch(getApiUrl('/v1/synthesize'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ text: 'test', voice: 'af_heart', speed: 1.0 }),
                            signal: controller.signal
                          });
                          clearTimeout(timeoutId);
                          if (response.ok) {
                            setBackendAvailable(true);
                            setIsLocalhost(true);
                            setStatus('API connected!');
                          } else {
                            throw new Error('Backend error');
                          }
                        } catch (e) {
                          setBackendAvailable(false);
                          setIsLocalhost(false);
                          setStatus('API unavailable');
                        }
                      }}
                      className={`text-[10px] font-bold px-2 py-1 rounded-lg ${theme.hover} ${theme.textSecondary} hover:text-blue-500 transition-colors`}
                    >
                      Recheck
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Reading Stats */}
            {pdfDoc && (
              <div className={`px-4 py-3 border-b ${theme.borderSecondary} ${darkMode ? 'bg-slate-800/30' : 'bg-blue-50/50'}`}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg flex items-center justify-center text-white text-xs font-black">
                      {calculateReadingProgress()}%
                    </div>
                    <div>
                      <p className={`text-[10px] font-black ${theme.textMuted} uppercase`}>Progress</p>
                      <p className={`text-xs font-bold ${theme.text}`}>
                        {currentSentenceIndex + 1} / {textItems.length} sentences
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Sidebar Tabs */}
            {pdfDoc && (
              <div className={`flex border-b ${theme.borderSecondary}`}>
                <button
                  onClick={() => setSidebarTab('sentences')}
                  className={`flex-1 py-2.5 text-xs font-bold flex items-center justify-center gap-2 transition-colors ${sidebarTab === 'sentences'
                    ? 'text-blue-500 border-b-2 border-blue-500'
                    : theme.textMuted + ' hover:text-blue-400'
                    }`}
                >
                  <List size={14} />
                  Sentences
                </button>
                {pdfOutline.length > 0 && (
                  <button
                    onClick={() => setSidebarTab('chapters')}
                    className={`flex-1 py-2.5 text-xs font-bold flex items-center justify-center gap-2 transition-colors ${sidebarTab === 'chapters'
                      ? 'text-blue-500 border-b-2 border-blue-500'
                      : theme.textMuted + ' hover:text-blue-400'
                      }`}
                  >
                    <BookOpen size={14} />
                    Chapters ({pdfOutline.length})
                  </button>
                )}
              </div>
            )}

            {/* Sentence List */}
            {sidebarTab === 'sentences' && (
              <div ref={sidebarRef} className={`flex-1 overflow-y-auto p-2 space-y-1 ${darkMode ? 'bg-slate-800/30' : 'bg-slate-50/30'} custom-scrollbar`}>
                <h3 className={`px-3 py-2 text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Page Contents</h3>
                {textItems.length === 0 && <p className={`text-xs ${theme.textMuted} p-3 italic`}>Upload a PDF to see text segments...</p>}
                {textItems.map((text, i) => (
                  <button
                    key={i}
                    ref={el => sentenceRefs.current[i] = el}
                    onClick={() => handleMobileSentenceClick(i)}
                    onContextMenu={(e) => handleSentenceContextMenu(e, i)}
                    className={`w-full text-left p-3 rounded-xl text-xs leading-relaxed transition-all ${currentSentenceIndex === i
                      ? 'bg-blue-600 text-white shadow-lg scale-[1.02] font-medium'
                      : `${theme.hover} ${theme.textSecondary} hover:shadow-sm`
                      }`}
                  >
                    <span className={`inline-block w-5 h-5 rounded-full text-center text-[10px] font-bold mr-2 leading-5 ${currentSentenceIndex === i
                      ? 'bg-white/20 text-white'
                      : `${theme.bgTertiary} ${theme.textMuted}`
                      }`}>
                      {i + 1}
                    </span>
                    {text.length > 100 ? text.slice(0, 100) + '...' : text}
                  </button>
                ))}
              </div>
            )}

            {/* Chapters List (TOC) */}
            {sidebarTab === 'chapters' && pdfOutline.length > 0 && (
              <div className={`flex-1 overflow-y-auto p-2 space-y-1 ${darkMode ? 'bg-slate-800/30' : 'bg-slate-50/30'} custom-scrollbar`}>
                <h3 className={`px-3 py-2 text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Table of Contents</h3>
                {pdfOutline.map((item, i) => (
                  <button
                    key={i}
                    onClick={async () => {
                      // Navigate to the chapter's destination page
                      if (item.dest) {
                        try {
                          let pageIndex;
                          if (typeof item.dest === 'string') {
                            // Named destination
                            const dest = await pdfDoc.getDestination(item.dest);
                            if (dest) {
                              const ref = dest[0];
                              pageIndex = await pdfDoc.getPageIndex(ref);
                            }
                          } else if (Array.isArray(item.dest)) {
                            // Direct destination
                            const ref = item.dest[0];
                            pageIndex = await pdfDoc.getPageIndex(ref);
                          }
                          if (pageIndex !== undefined) {
                            setCurrentPage(pageIndex + 1);
                            setCurrentSentenceIndex(-1);
                            setStatus(`Jumped to: ${item.title}`);
                          }
                        } catch (e) {
                          console.warn('Could not navigate to chapter:', e);
                        }
                      }
                    }}
                    className={`w-full text-left p-3 rounded-xl text-xs leading-relaxed transition-all ${theme.hover} ${theme.textSecondary} hover:shadow-sm hover:text-blue-500`}
                  >
                    <BookOpen size={12} className="inline mr-2 opacity-50" />
                    {item.title}
                  </button>
                ))}
              </div>
            )}
          </div>
        </aside>

        {/* VIEWPORT: PDF CANVAS */}
        <section className={`flex-1 flex flex-col overflow-hidden ${theme.viewportBg} transition-colors duration-300`}>

          {/* PDF OPTIONS TOOLBAR */}
          {pdfDoc && (
            <div className={`flex items-center justify-between px-4 py-2 ${theme.bgSecondary} border-b ${theme.border} shrink-0`}>
              {/* LEFT: Page Navigation */}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                  disabled={currentPage <= 1}
                  className={`p-2 rounded-lg transition-all ${theme.hover} ${currentPage <= 1 ? 'opacity-30 cursor-not-allowed' : theme.textSecondary + ' hover:text-blue-500'}`}
                  title="Previous Page"
                >
                  <ChevronLeft size={18} />
                </button>
                <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg ${theme.bgTertiary} border ${theme.border}`}>
                  <input
                    type="number"
                    min="1"
                    max={numPages}
                    value={currentPage}
                    onChange={(e) => {
                      const val = parseInt(e.target.value);
                      if (val >= 1 && val <= numPages) setCurrentPage(val);
                    }}
                    className={`w-12 text-center text-sm font-bold ${theme.bgTertiary} ${theme.text} outline-none`}
                  />
                  <span className={`text-sm ${theme.textMuted}`}>/ {numPages}</span>
                </div>
                <button
                  onClick={() => setCurrentPage(p => Math.min(numPages, p + 1))}
                  disabled={currentPage >= numPages}
                  className={`p-2 rounded-lg transition-all ${theme.hover} ${currentPage >= numPages ? 'opacity-30 cursor-not-allowed' : theme.textSecondary + ' hover:text-blue-500'}`}
                  title="Next Page"
                >
                  <ChevronRight size={18} />
                </button>
              </div>

              {/* CENTER: Zoom Controls */}
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setScale(s => Math.max(0.5, s - 0.2))}
                  className={`p-2 rounded-lg transition-all ${theme.hover} ${theme.textSecondary} hover:text-blue-500`}
                  title="Zoom Out (Ctrl+-)"
                >
                  <ZoomOut size={18} />
                </button>
                <div className={`px-3 py-1.5 rounded-lg ${theme.bgTertiary} border ${theme.border} min-w-[70px] text-center`}>
                  <span className={`text-sm font-bold ${theme.text}`}>{Math.round(scale * 100)}%</span>
                </div>
                <button
                  onClick={() => setScale(s => Math.min(3, s + 0.2))}
                  className={`p-2 rounded-lg transition-all ${theme.hover} ${theme.textSecondary} hover:text-blue-500`}
                  title="Zoom In (Ctrl++)"
                >
                  <ZoomIn size={18} />
                </button>
                <div className={`w-px h-6 ${theme.border} mx-2`}></div>
                <button
                  onClick={() => setScale(1.0)}
                  className={`p-2 rounded-lg transition-all ${theme.hover} ${theme.textSecondary} hover:text-blue-500`}
                  title="Reset Zoom"
                >
                  <RotateCcw size={16} />
                </button>
              </div>

              {/* RIGHT: Fit Options */}
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setScale(0.8)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} ${scale === 0.8 ? 'bg-blue-600 text-white' : theme.textSecondary}`}
                  title="Fit Page"
                >
                  <Minimize size={14} className="inline mr-1" />
                  Fit
                </button>
                <button
                  onClick={() => setScale(1.2)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} ${scale === 1.2 ? 'bg-blue-600 text-white' : theme.textSecondary}`}
                  title="Fit Width"
                >
                  <Maximize size={14} className="inline mr-1" />
                  Width
                </button>
              </div>
            </div>
          )}

          {/* PDF CANVAS CONTAINER */}
          <div
            ref={pdfContainerRef}
            className={`flex-1 overflow-auto p-2 md:p-6 flex justify-center custom-scrollbar ${darkMode ? 'bg-slate-900/50' : 'bg-slate-300/30'} ${isMobile && pdfDoc ? 'pb-28' : ''}`}
          >
            <div
              className={`relative ${theme.canvasBg} shadow-2xl rounded-sm border ${theme.border}`}
              style={{
                height: 'fit-content',
                maxWidth: isMobile ? '100%' : undefined,
              }}
            >
              {pdfDoc ? (
                <>
                  <canvas
                    ref={canvasRef}
                    className="block"
                    style={{
                      maxWidth: isMobile ? '100%' : undefined,
                      height: isMobile ? 'auto' : undefined,
                    }}
                  />
                  <div
                    ref={textLayerRef}
                    className="textLayer absolute top-0 left-0 overflow-hidden opacity-25 leading-none"
                    style={{ pointerEvents: 'auto' }}
                  />
                </>
              ) : (
                <div className={`flex flex-col items-center justify-center p-6 md:p-12 text-center gap-6 ${theme.canvasBg} min-h-[400px] md:min-h-[600px] w-full md:min-w-[500px]`}>
                  <div
                    className={`w-20 h-20 ${theme.bgTertiary} rounded-2xl flex items-center justify-center ${theme.textMuted} border-2 border-dashed ${theme.border} cursor-pointer hover:border-blue-400 hover:text-blue-500 transition-all`}
                    onClick={() => fileInputRef.current.click()}
                  >
                    <Upload size={36} />
                  </div>
                  <div>
                    <p className={`${theme.textSecondary} font-semibold mb-2 text-lg`}>Open a PDF Document</p>
                    <p className={`text-sm ${theme.textMuted}`}>Click to browse or drag & drop</p>
                  </div>
                  <button
                    onClick={() => fileInputRef.current.click()}
                    className="px-6 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-md"
                  >
                    <Upload size={16} className="inline mr-2" />
                    Choose File
                  </button>

                  {/* LIBRARY - Recent Books */}
                  {recentBooks.length > 0 && (
                    <div className={`w-full max-w-md mt-4 border-t ${theme.borderSecondary} pt-6`}>
                      <div className="flex items-center gap-2 mb-4 justify-center">
                        <Library size={18} className={theme.textMuted} />
                        <h3 className={`text-sm font-bold ${theme.textSecondary}`}>Your Library</h3>
                      </div>
                      <div className="space-y-2">
                        {recentBooks.map((book) => (
                          <button
                            key={book.fileName}
                            onClick={() => openFromLibrary(book.fileName)}
                            className={`w-full flex items-center justify-between p-3 rounded-xl ${theme.bgTertiary} ${theme.hover} transition-all group border ${theme.border}`}
                          >
                            <div className="flex items-center gap-3 text-left">
                              <div className={`w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-cyan-600 flex items-center justify-center text-white flex-shrink-0`}>
                                <BookOpen size={18} />
                              </div>
                              <div className="overflow-hidden">
                                <p className={`font-medium text-sm ${theme.text} truncate max-w-[200px]`}>
                                  {book.fileName.replace('.pdf', '')}
                                </p>
                                <p className={`text-xs ${theme.textMuted}`}>
                                  {(book.size / 1024 / 1024).toFixed(1)} MB • {new Date(book.lastOpened).toLocaleDateString()}
                                </p>
                              </div>
                            </div>
                            <button
                              onClick={(e) => removeFromLibrary(book.fileName, e)}
                              className={`p-2 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity ${theme.hover} hover:text-red-500`}
                              title="Remove from library"
                            >
                              <Trash2 size={14} />
                            </button>
                          </button>
                        ))}
                      </div>
                      <p className={`text-[10px] ${theme.textMuted} mt-3 italic`}>
                        Last {recentBooks.length} books saved for instant resume
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </section>
      </main>

      {/* MOBILE BOTTOM NAVIGATION */}
      {isMobile && pdfDoc && (
        <nav className={`fixed bottom-0 left-0 right-0 z-50 ${theme.bgSecondary} border-t ${theme.border} px-2 py-2 safe-area-pb`}>
          <div className="flex items-center justify-around gap-1">
            {/* Previous Page */}
            <button
              onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
              disabled={currentPage <= 1}
              className={`p-3 rounded-xl transition-all ${currentPage <= 1 ? 'opacity-30' : theme.hover + ' ' + theme.textSecondary}`}
            >
              <ChevronLeft size={24} />
            </button>

            {/* Previous Sentence */}
            <button
              onClick={() => setCurrentSentenceIndex(prev => Math.max(-1, prev - 2))}
              className={`p-3 rounded-xl ${theme.hover} ${theme.textSecondary}`}
            >
              <SkipBack size={22} />
            </button>

            {/* Play/Pause */}
            <button
              onClick={handlePlayPause}
              className={`p-4 rounded-2xl flex items-center justify-center transition-all ${isPlaying
                ? 'bg-amber-500 text-white shadow-lg shadow-amber-500/30'
                : 'bg-gradient-to-r from-blue-500 to-cyan-600 text-white shadow-lg shadow-blue-500/30'
                }`}
            >
              {isPlaying ? <Pause size={28} /> : <Play size={28} className="ml-1" />}
            </button>

            {/* Next Sentence */}
            <button
              onClick={skipToNextSentence}
              className={`p-3 rounded-xl ${theme.hover} ${theme.textSecondary}`}
            >
              <SkipForward size={22} />
            </button>

            {/* Next Page */}
            <button
              onClick={() => setCurrentPage(p => Math.min(numPages, p + 1))}
              disabled={currentPage >= numPages}
              className={`p-3 rounded-xl transition-all ${currentPage >= numPages ? 'opacity-30' : theme.hover + ' ' + theme.textSecondary}`}
            >
              <ChevronRight size={24} />
            </button>
          </div>

          {/* Progress Indicator */}
          <div className={`mt-2 mx-4 text-center text-[10px] font-bold ${theme.textMuted}`}>
            Page {currentPage}/{numPages} • Sentence {currentSentenceIndex + 1}/{textItems.length}
          </div>
        </nav>
      )}

      <style dangerouslySetInnerHTML={{
        __html: `
        .custom-scrollbar::-webkit-scrollbar { width: 6px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: ${darkMode ? '#475569' : '#cbd5e1'}; border-radius: 10px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: ${darkMode ? '#64748b' : '#94a3b8'}; }
        
        input[type="range"]::-webkit-slider-thumb {
          -webkit-appearance: none;
          width: 14px;
          height: 14px;
          border-radius: 50%;
          background: linear-gradient(135deg, #3b82f6, #06b6d4);
          cursor: pointer;
          box-shadow: 0 2px 6px rgba(59, 130, 246, 0.4);
        }
        
        /* PDF.js Text Layer for selection */
        .textLayer {
          position: absolute;
          left: 0;
          top: 0;
          right: 0;
          bottom: 0;
          overflow: hidden;
          line-height: 1.0;
          text-align: initial;
          opacity: 0.2;
        }
        .textLayer > span {
          color: transparent;
          position: absolute;
          white-space: pre;
          cursor: text;
          transform-origin: 0% 0%;
        }
        .textLayer ::selection {
          background: rgba(59, 130, 246, 0.5);
        }
        .textLayer ::-moz-selection {
          background: rgba(59, 130, 246, 0.5);
        }
        
        /* Mobile optimizations */
        .safe-area-pb {
          padding-bottom: max(12px, env(safe-area-inset-bottom));
        }
        
        @media (max-width: 767px) {
          /* Larger touch targets on mobile */
          button {
            min-height: 44px;
            min-width: 44px;
          }
          
          /* Hide header playback controls on mobile (we have bottom nav) */
          .header-playback-controls {
            display: none;
          }
          
          /* Adjust PDF container padding for bottom nav */
          .pdf-container-mobile {
            padding-bottom: 100px;
          }
          
          /* Larger font for better readability */
          select, input[type="text"] {
            font-size: 16px !important; /* Prevents zoom on iOS */
          }
        }
        
        /* Touch-friendly slider on mobile */
        @media (pointer: coarse) {
          input[type="range"]::-webkit-slider-thumb {
            width: 24px;
            height: 24px;
          }
          
          input[type="range"] {
            height: 8px;
          }
        }
      `}} />
    </div >
  );
}