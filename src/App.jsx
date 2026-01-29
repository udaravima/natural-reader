import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Pause, Square, Upload, ChevronLeft, ChevronRight, Settings, Volume2, Globe, Cpu } from 'lucide-react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

// --- CONFIGURATION ---
// Configure PDF.js worker using the bundled worker from pdfjs-dist package
pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker;

// Default voices available in Kokoro (map these to your UI)
const KOKORO_VOICES = [
  { id: 'af_heart', name: 'Heart (US Female)' },
  { id: 'af_bella', name: 'Bella (US Female)' },
  { id: 'am_michael', name: 'Michael (US Male)' },
  { id: 'bf_emma', name: 'Emma (UK Female)' },
  { id: 'bf_alice', name: 'Alice (UK Female)' },
  { id: 'bm_lewis', name: 'Lewis (UK Male)' },
];

export default function App() {
  // State: PDF Data
  const [pdfDoc, setPdfDoc] = useState(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1.2);
  const [textItems, setTextItems] = useState([]); // Extracted text for the current page

  // State: Playback
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentSentenceIndex, setCurrentSentenceIndex] = useState(-1);
  const [audioUrl, setAudioUrl] = useState(null);
  const [playbackSpeed, setPlaybackSpeed] = useState(1.0);
  const [selectedVoice, setSelectedVoice] = useState('af_heart');
  const [isLocalhost, setIsLocalhost] = useState(false); // Toggle between WebSpeech (Sim) and Localhost (Real)
  const [status, setStatus] = useState('Ready');

  // Refs
  const canvasRef = useRef(null);
  const audioRef = useRef(new Audio());
  const fileInputRef = useRef(null);
  const sentenceIndexRef = useRef(-1); // Track current index to avoid stale closures

  // --- PDF RENDERING ENGINE ---

  const renderPage = async (pageNum, doc) => {
    if (!doc) return;
    try {
      const page = await doc.getPage(pageNum);

      // 1. Render Visuals (Canvas)
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      canvas.height = viewport.height;
      canvas.width = viewport.width;

      const renderContext = {
        canvasContext: context,
        viewport: viewport,
      };
      await page.render(renderContext).promise;

      // 2. Extract Text (for TTS)
      const textContent = await page.getTextContent();
      // Simple heuristic: Join items, then split by periods/newlines to approximate sentences
      const rawText = textContent.items.map(item => item.str).join(' ');
      // Clean up multiple spaces and split into "sentences" for chunking
      const sentences = rawText
        .replace(/\s+/g, ' ')
        .split(/(?<=[.!?])\s+/)
        .filter(s => s.trim().length > 0);

      setTextItems(sentences);
      setCurrentSentenceIndex(-1); // Reset reading position
    } catch (err) {
      console.error("Render error:", err);
      setStatus("Error rendering page");
    }
  };

  useEffect(() => {
    if (pdfDoc) renderPage(currentPage, pdfDoc);
  }, [pdfDoc, currentPage, scale]);

  // --- FILE HANDLING ---

  const handleFileUpload = (event) => {
    const file = event.target.files[0];
    if (file && file.type === 'application/pdf') {
      const reader = new FileReader();
      reader.onload = async (e) => {
        try {
          setStatus("Loading PDF...");
          const loadingTask = pdfjsLib.getDocument({ data: e.target.result });
          const doc = await loadingTask.promise;
          setPdfDoc(doc);
          setNumPages(doc.numPages);
          setCurrentPage(1);
          setStatus("PDF Loaded");
        } catch (err) {
          setStatus("Error parsing PDF");
          console.error(err);
        }
      };
      reader.readAsArrayBuffer(file);
    }
  };

  // --- AUDIO ENGINE (THE BRIDGE) ---

  const fetchAudioFromLocalhost = async (text) => {
    try {
      const response = await fetch('http://localhost:8000/v1/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: text,
          voice: selectedVoice,
          speed: playbackSpeed
        })
      });

      if (!response.ok) throw new Error("Backend error");
      const data = await response.json();

      // Convert Base64 to Blob URL
      const byteCharacters = atob(data.audio_base64);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      const blob = new Blob([byteArray], { type: 'audio/wav' });
      return URL.createObjectURL(blob);
    } catch (err) {
      console.error(err);
      setStatus("Connection Failed: Ensure server.py is running on port 8000");
      return null;
    }
  };

  // Keep ref in sync with state for use in closures
  useEffect(() => {
    sentenceIndexRef.current = currentSentenceIndex;
  }, [currentSentenceIndex]);

  // The "Game Loop" for reading
  useEffect(() => {
    if (!isPlaying) return;

    let isCancelled = false; // Prevent race conditions on cleanup

    const playNextSentence = async () => {
      if (isCancelled) return;

      // Use ref to get the CURRENT index (avoids stale closure)
      const currentIdx = sentenceIndexRef.current;
      const nextIndex = currentIdx + 1;

      // If end of page, try next page
      if (nextIndex >= textItems.length) {
        if (currentPage < numPages) {
          setCurrentPage(p => p + 1);
          setIsPlaying(false);
          setStatus("Page Complete");
          return;
        } else {
          setIsPlaying(false);
          setStatus("Finished");
          return;
        }
      }

      // Update both state and ref
      sentenceIndexRef.current = nextIndex;
      setCurrentSentenceIndex(nextIndex);
      const textToRead = textItems[nextIndex];

      if (isLocalhost) {
        // --- REAL KOKORO BACKEND ---
        setStatus(`Generating: "${textToRead.substring(0, 20)}..."`);
        const url = await fetchAudioFromLocalhost(textToRead);

        if (url && !isCancelled) {
          setStatus("Playing...");
          audioRef.current.src = url;
          audioRef.current.playbackRate = 1.0;
          audioRef.current.onended = () => {
            if (!isCancelled) playNextSentence();
          };
          audioRef.current.play();
        } else if (!url) {
          setIsPlaying(false);
        }
      } else {
        // --- BROWSER SIMULATION (Web Speech API) ---
        setStatus("Speaking (Simulated)...");
        const utterance = new SpeechSynthesisUtterance(textToRead);
        utterance.rate = playbackSpeed;
        utterance.onend = () => {
          if (!isCancelled) playNextSentence();
        };
        window.speechSynthesis.speak(utterance);
      }
    };

    // Kick off playback
    if (audioRef.current.paused && !window.speechSynthesis.speaking) {
      playNextSentence();
    }

    // Cleanup
    return () => {
      isCancelled = true;
      window.speechSynthesis.cancel();
      audioRef.current.pause();
      audioRef.current.onended = null;
    };
  }, [isPlaying, isLocalhost, textItems, currentPage, numPages, playbackSpeed]);


  // --- UI COMPONENTS ---

  return (
    <div className="flex flex-col h-screen bg-slate-100 text-slate-800 font-sans">

      {/* HEADER / RIBBON */}
      <header className="bg-white border-b border-slate-200 px-6 py-3 flex items-center justify-between shadow-sm z-10">
        <div className="flex items-center gap-4">
          <div className="bg-blue-600 text-white p-2 rounded-lg">
            <Volume2 size={24} />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-slate-700">Neural Reader</h1>
        </div>

        <div className="flex items-center gap-3 bg-slate-100 p-1.5 rounded-full border border-slate-200">
          {/* Controls */}
          <button
            onClick={() => {
              if (isPlaying) {
                setIsPlaying(false);
                window.speechSynthesis.cancel();
                audioRef.current.pause();
              } else {
                setIsPlaying(true);
              }
            }}
            className={`p-3 rounded-full transition-all ${isPlaying ? 'bg-amber-100 text-amber-600' : 'bg-blue-600 text-white shadow-md hover:bg-blue-700'}`}
          >
            {isPlaying ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" className="ml-0.5" />}
          </button>

          <button onClick={() => { setIsPlaying(false); setCurrentSentenceIndex(-1); }} className="p-2 text-slate-500 hover:text-red-500 hover:bg-red-50 rounded-full">
            <Square size={18} fill="currentColor" />
          </button>

          <div className="h-6 w-px bg-slate-300 mx-1"></div>

          <button onClick={() => setCurrentPage(Math.max(1, currentPage - 1))} className="p-2 text-slate-600 hover:bg-white rounded-full"><ChevronLeft size={20} /></button>
          <span className="text-sm font-medium w-16 text-center">{currentPage} / {numPages || '-'}</span>
          <button onClick={() => setCurrentPage(Math.min(numPages, currentPage + 1))} className="p-2 text-slate-600 hover:bg-white rounded-full"><ChevronRight size={20} /></button>
        </div>

        <div className="flex items-center gap-4">
          {/* Backend Toggle */}
          <div className="flex items-center gap-2 text-xs font-medium bg-slate-50 border px-3 py-1.5 rounded-lg">
            <span className={!isLocalhost ? "text-blue-600" : "text-slate-400"}>Browser Sim</span>
            <button
              onClick={() => setIsLocalhost(!isLocalhost)}
              className={`w-8 h-4 rounded-full p-0.5 transition-colors ${isLocalhost ? 'bg-green-500' : 'bg-slate-300'}`}
            >
              <div className={`w-3 h-3 bg-white rounded-full shadow-sm transform transition-transform ${isLocalhost ? 'translate-x-4' : 'translate-x-0'}`} />
            </button>
            <span className={isLocalhost ? "text-green-600" : "text-slate-400"}>Localhost:8000</span>
          </div>

          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileUpload}
            accept="application/pdf"
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current.click()}
            className="flex items-center gap-2 px-4 py-2 bg-slate-800 text-white text-sm font-medium rounded-lg hover:bg-slate-700 transition-all"
          >
            <Upload size={16} /> Open PDF
          </button>
        </div>
      </header>

      {/* MAIN CONTENT AREA */}
      <main className="flex-1 flex overflow-hidden">

        {/* SIDEBAR: Configuration */}
        <aside className="w-72 bg-white border-r border-slate-200 p-6 flex flex-col gap-8 overflow-y-auto">
          <div>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-4">Voice Settings</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-2">Voice Model</label>
                <select
                  value={selectedVoice}
                  onChange={(e) => setSelectedVoice(e.target.value)}
                  className="w-full text-sm border-slate-300 rounded-md shadow-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 p-2 border"
                >
                  {KOKORO_VOICES.map(v => (
                    <option key={v.id} value={v.id}>{v.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-2">Speed ({playbackSpeed}x)</label>
                <input
                  type="range"
                  min="0.5"
                  max="2.0"
                  step="0.1"
                  value={playbackSpeed}
                  onChange={(e) => setPlaybackSpeed(parseFloat(e.target.value))}
                  className="w-full accent-blue-600"
                />
              </div>
            </div>
          </div>

          <div className="flex-1">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-4">Status Log</h3>
            <div className="bg-slate-900 text-green-400 text-xs font-mono p-3 rounded-lg h-full overflow-hidden">
              <p className="opacity-50 mb-2">System initialized...</p>
              <p> {status}</p>
              {isPlaying && (
                <div className="mt-2 flex gap-1">
                  <span className="animate-pulse">●</span>
                  <span className="animate-pulse delay-75">●</span>
                  <span className="animate-pulse delay-150">●</span>
                </div>
              )}
            </div>
          </div>
        </aside>

        {/* PDF VIEWPORT */}
        <section className="flex-1 bg-slate-100 overflow-auto flex justify-center p-8 relative">
          <div className="relative shadow-2xl transition-all duration-300">
            {/* Visual Canvas */}
            <canvas ref={canvasRef} className="rounded-sm bg-white" />

            {/* Immersive Reader Overlay (Dynamic Highlighting) */}
            {/* In a full implementation, we would map specific coordinates. 
                   For this "Mental Model" demo, we overlay the active text below the PDF or 
                   in a floating card to show we are tracking state.
                */}
          </div>

          {/* Active Sentence Display (The "Closed Caption" style reader) */}
          {isPlaying && currentSentenceIndex >= 0 && (
            <div className="absolute bottom-8 left-1/2 -translate-x-1/2 w-3/4 max-w-2xl bg-slate-900/90 backdrop-blur-sm text-white p-6 rounded-xl shadow-2xl border border-white/10 z-50 text-center transition-all">
              <p className="text-lg leading-relaxed font-medium">
                {textItems[currentSentenceIndex]}
              </p>
              <div className="text-xs text-slate-400 mt-3 font-mono">
                Segment {currentSentenceIndex + 1} / {textItems.length}
              </div>
            </div>
          )}
        </section>

      </main>
    </div>
  );
}