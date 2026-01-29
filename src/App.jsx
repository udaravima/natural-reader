import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Play, Pause, Square, Upload, ChevronLeft, ChevronRight,
  Volume2, SkipForward, SkipBack, Zap, Loader2, Moon, Sun,
  ZoomIn, ZoomOut, Keyboard, Clock, VolumeX, Volume1,
  Maximize, Minimize, RotateCcw
} from 'lucide-react';

// Default voices available in Kokoro
const KOKORO_VOICES = [
  { id: 'af_heart', name: 'Heart (US Female)' },
  { id: 'af_bella', name: 'Bella (US Female)' },
  { id: 'af_alloy', name: 'Alloy (US Female)' },
  { id: 'af_aoede', name: 'Aoede (US Female)' },
  { id: 'af_jessica', name: 'Jessica (US Female)' },
  { id: 'af_kore', name: 'Kore (US Female)' },
  { id: 'af_nicole', name: 'Nicole (US Female)' },
  { id: 'af_nova', name: 'Nova (US Female)' },
  { id: 'af_river', name: 'River (US Male)' },
  { id: 'af_sarah', name: 'Sarah (US Female)' },
  { id: 'af_sky', name: 'Sky (US Female)' },
  { id: 'am_michael', name: 'Michael (US Male)' },
  { id: 'am_adam', name: 'Adam (US Male)' },
  { id: 'am_echo', name: 'Echo (US Male)' },
  { id: 'am_eric', name: 'Eric (US Male)' },
  { id: 'am_fenrir', name: 'Fenrir (US Male)' },
  { id: 'am_liam', name: 'Liam (US Male)' },
  { id: 'am_onyx', name: 'Onyx (US Male)' },
  { id: 'am_puck', name: 'Puck (US Male)' },
  { id: 'bf_emma', name: 'Emma (UK Female)' },
  { id: 'bf_alice', name: 'Alice (UK Female)' },
  { id: 'bf_isabella', name: 'Isabella (UK Female)' },
  { id: 'bf_lily', name: 'Lily (UK Female)' },
  { id: 'bm_daniel', name: 'Daniel (UK Male)' },
  { id: 'bm_fable', name: 'Fable (UK Male)' },
  { id: 'bm_george', name: 'George (UK Male)' },
  { id: 'bm_lewis', name: 'Lewis (UK Male)' },
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
  const [status, setStatus] = useState('Initializing PDF Engine...');

  // Enhanced Features State
  const [darkMode, setDarkMode] = useState(() => getStoredValue('darkMode', false));
  const [volume, setVolume] = useState(() => getStoredValue('volume', 1.0));
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [readingStartTime, setReadingStartTime] = useState(null);
  const [totalWordsRead, setTotalWordsRead] = useState(0);
  const [fitMode, setFitMode] = useState('custom');
  const pdfContainerRef = useRef(null);

  // Buffer Management
  const audioCache = useRef(new Map());
  const audioRef = useRef(new Audio());
  const canvasRef = useRef(null);
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
    const script = document.createElement('script');
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js';
    script.async = true;
    script.onload = () => {
      const pdfjsLib = window['pdfjs-dist/build/pdf'];
      pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
      pdfjsLibRef.current = pdfjsLib;
      setIsLibLoaded(true);
      setStatus('Ready to Open PDF');
    };
    document.head.appendChild(script);
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

      const textContent = await page.getTextContent();
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

  const handleFileUpload = (e) => {
    const file = e.target.files[0];
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
        } catch (err) {
          setStatus("Error loading PDF");
        }
      };
      reader.readAsArrayBuffer(file);
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
      const response = await fetch('http://localhost:8000/v1/synthesize', {
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
    <div className={`flex flex-col h-screen ${theme.bg} ${theme.text} font-sans ${theme.selection} transition-colors duration-300`}>

      {/* READING PROGRESS BAR */}
      {pdfDoc && (
        <div className="h-1 bg-slate-300/20 w-full fixed top-0 left-0 z-50">
          <div
            className="h-full bg-gradient-to-r from-blue-500 via-cyan-500 to-teal-500 transition-all duration-500 ease-out"
            style={{ width: `${calculateReadingProgress()}%` }}
          />
        </div>
      )}

      {/* HEADER / CONTROL BAR */}
      <header className={`h-16 ${theme.bgSecondary} border-b ${theme.border} px-6 flex items-center justify-between z-20 sticky top-0 shadow-sm transition-colors duration-300`}>
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-cyan-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-blue-500/30">
            <Volume2 size={22} />
          </div>
          <div className="hidden sm:block">
            <h1 className={`text-sm font-bold uppercase tracking-tighter ${theme.textSecondary}`}>Neural PDF</h1>
            <p className="text-[10px] font-bold text-blue-500 truncate max-w-[200px]">{status}</p>
          </div>
        </div>

        {/* PLAYBACK CONTROLS */}
        <div className={`flex items-center gap-2 ${theme.bgTertiary} p-1 rounded-xl border ${theme.border} shadow-inner`}>
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
        <div className="flex items-center gap-3">
          {/* Estimated Time */}
          {pdfDoc && calculateEstimatedTimeRemaining() && (
            <div className={`hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-lg border ${theme.border} text-[10px] font-bold ${theme.textSecondary}`}>
              <Clock size={12} />
              {calculateEstimatedTimeRemaining()}
            </div>
          )}

          {/* TTS Mode Toggle */}
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-[10px] font-black tracking-widest transition-colors ${isLocalhost
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

          {/* Keyboard Shortcuts */}
          <button
            onClick={() => setShowShortcuts(!showShortcuts)}
            className={`p-2.5 ${theme.bgTertiary} rounded-xl ${theme.hover} transition-all ${theme.textSecondary} hover:text-blue-500`}
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

      <main className="flex-1 flex overflow-hidden">

        {/* SIDEBAR: NAVIGATION & SETTINGS */}
        <aside className={`w-80 ${theme.bgSecondary} border-r ${theme.border} flex flex-col shadow-xl z-10 transition-colors duration-300`}>
          <div className={`p-4 border-b ${theme.borderSecondary} ${darkMode ? 'bg-slate-800/50' : 'bg-slate-50/50'}`}>
            <h3 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest mb-3`}>Settings</h3>
            <div className="flex flex-col gap-3">
              {/* Voice Selection */}
              <div className="space-y-1">
                <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>VOICE</span>
                <select
                  value={selectedVoice}
                  onChange={(e) => { setSelectedVoice(e.target.value); clearCache(); }}
                  className={`w-full text-xs font-bold p-2.5 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors`}
                >
                  {KOKORO_VOICES.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
                </select>
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

          {/* Sentence List */}
          <div ref={sidebarRef} className={`flex-1 overflow-y-auto p-2 space-y-1 ${darkMode ? 'bg-slate-800/30' : 'bg-slate-50/30'} custom-scrollbar`}>
            <h3 className={`px-3 py-2 text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Page Contents</h3>
            {textItems.length === 0 && <p className={`text-xs ${theme.textMuted} p-3 italic`}>Upload a PDF to see text segments...</p>}
            {textItems.map((text, i) => (
              <button
                key={i}
                ref={el => sentenceRefs.current[i] = el}
                onClick={() => { setCurrentSentenceIndex(i - 1); setIsPlaying(true); }}
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
            className={`flex-1 overflow-auto p-6 flex justify-center custom-scrollbar ${darkMode ? 'bg-slate-900/50' : 'bg-slate-300/30'}`}
          >
            <div className={`relative ${theme.canvasBg} shadow-2xl rounded-sm border ${theme.border}`} style={{ height: 'fit-content' }}>
              {pdfDoc ? (
                <canvas ref={canvasRef} className="block" />
              ) : (
                <div className={`flex flex-col items-center justify-center p-24 text-center gap-6 ${theme.canvasBg} min-h-[600px] min-w-[450px]`}>
                  <div
                    className={`w-24 h-24 ${theme.bgTertiary} rounded-2xl flex items-center justify-center ${theme.textMuted} border-2 border-dashed ${theme.border} cursor-pointer hover:border-blue-400 hover:text-blue-500 transition-all`}
                    onClick={() => fileInputRef.current.click()}
                  >
                    <Upload size={40} />
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
                </div>
              )}
            </div>
          </div>
        </section>
      </main>

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
      `}} />
    </div>
  );
}