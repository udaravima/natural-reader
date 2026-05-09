import {
    ChevronDown, ChevronUp, Settings, PlayCircle, Square,
    Clock, List, BookOpen, VolumeX, Volume1, Volume2
} from 'lucide-react';
import { KOKORO_VOICES } from '../constants';

export default function Sidebar({
    theme,
    darkMode,
    effectiveIsMobile,
    sidebarOpen,
    settingsOpen, setSettingsOpen,
    sidebarTab, setSidebarTab,
    // Settings
    selectedVoice, setSelectedVoice,
    playbackSpeed, setPlaybackSpeed,
    volume, setVolume,
    isLocalhost, setIsLocalhost,
    apiHost, setApiHost,
    apiPort, setApiPort,
    requestTimeout, setRequestTimeout,
    unlimitedBatchTimeout, setUnlimitedBatchTimeout,
    backendAvailable, setBackendAvailable,
    layoutMode, setLayoutMode,
    mobileBreakpoint, setMobileBreakpoint,
    showHeaderControlsOnMobile, setShowHeaderControlsOnMobile,
    // Voice preview
    isPreviewingVoice,
    previewVoice,
    stopVoicePreview,
    // PDF state
    hasDocument,
    pdfDoc,
    pdfOutline,
    textItems,
    currentSentenceIndex,
    sentenceRefs,
    // Actions
    clearCache,
    checkBackend,
    setStatus,
    calculateReadingProgress,
    handleMobileSentenceClick,
    handleSentenceContextMenu,
    handleChapterNavigation,
}) {
    const VolumeIcon = volume === 0 ? VolumeX : volume < 0.5 ? Volume1 : Volume2;
    const currentVoice = KOKORO_VOICES.find(v => v.id === selectedVoice);

    return (
        <aside className={`
      ${effectiveIsMobile
                ? `fixed inset-y-0 left-0 z-40 w-80 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`
                : `relative ${sidebarOpen ? 'w-80' : 'w-0'} overflow-hidden`
            }
      ${theme.bgSecondary} border-r ${theme.border} flex flex-col shadow-xl transition-all duration-300 ease-in-out
      ${effectiveIsMobile && sidebarOpen ? 'pt-16' : ''}
    `}>
            <div className={`${effectiveIsMobile ? '' : 'w-80'} flex flex-col h-full`}>
                {/* Settings Toggle */}
                <div className={`border-b ${theme.borderSecondary} ${darkMode ? 'bg-slate-800/50' : 'bg-slate-50/50'}`}>
                    <button
                        onClick={() => setSettingsOpen(prev => !prev)}
                        className={`w-full flex items-center justify-between p-4 cursor-pointer hover:opacity-80 transition-opacity`}
                    >
                        <div className="flex items-center gap-2">
                            <Settings size={14} className={theme.textMuted} />
                            <h3 className={`text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Settings</h3>
                        </div>
                        {settingsOpen ? <ChevronUp size={14} className={theme.textMuted} /> : <ChevronDown size={14} className={theme.textMuted} />}
                    </button>
                    <div className={`overflow-hidden transition-all duration-300 ease-in-out ${settingsOpen ? 'max-h-[70vh] opacity-100' : 'max-h-0 opacity-0'}`}>
                        <div className="px-4 pb-4 flex flex-col gap-3 overflow-y-auto max-h-[calc(70vh-3rem)]">
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
                                {currentVoice?.sampleText && (
                                    <p className={`text-[10px] leading-relaxed ${theme.textMuted} italic px-1 py-2 rounded-lg ${theme.bgTertiary} border ${theme.border}`}>
                                        "{currentVoice.sampleText}"
                                    </p>
                                )}
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
                                            const ok = await checkBackend();
                                            if (ok) {
                                                setIsLocalhost(true);
                                                setStatus('API connected!');
                                            } else {
                                                setStatus('API unavailable');
                                            }
                                        }}
                                        className={`text-[10px] font-bold px-2 py-1 rounded-lg ${theme.hover} ${theme.textSecondary} hover:text-blue-500 transition-colors`}
                                    >
                                        Recheck
                                    </button>
                                </div>
                            </div>

                            {/* Request Timeout */}
                            <div className="space-y-1">
                                <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>REQUEST TIMEOUT</span>
                                <div className={`flex items-center gap-2 p-2.5 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
                                    <Clock size={14} className={theme.textSecondary} />
                                    <input
                                        type="number"
                                        min="5"
                                        max="120"
                                        value={requestTimeout}
                                        onChange={(e) => setRequestTimeout(Math.max(5, Math.min(120, parseInt(e.target.value) || 15)))}
                                        className={`w-14 text-xs font-bold text-center ${theme.bgTertiary} ${theme.text} rounded-lg p-1.5 outline-none focus:ring-2 focus:ring-blue-500`}
                                        title="Request timeout in seconds (5-120)"
                                    />
                                    <span className={`text-[10px] font-bold ${theme.textMuted}`}>seconds</span>
                                </div>
                                <p className={`text-[9px] ${theme.textMuted} px-1`}>
                                    Max wait time per TTS request. Increase if on slow network.
                                </p>
                                <label className={`flex items-center gap-2 mt-1.5 cursor-pointer`}>
                                    <input
                                        type="checkbox"
                                        checked={unlimitedBatchTimeout}
                                        onChange={(e) => setUnlimitedBatchTimeout(e.target.checked)}
                                        className="accent-blue-500 w-3.5 h-3.5"
                                    />
                                    <span className={`text-[10px] font-bold ${theme.textMuted}`}>Unlimited batch/download timeout</span>
                                </label>
                            </div>

                            {/* Mobile Layout Configuration */}
                            <div className="space-y-2">
                                <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>LAYOUT MODE</span>
                                <div className="space-y-2">
                                    <div className="flex items-center gap-2">
                                        <span className={`text-[10px] font-bold ${theme.textMuted} w-10 shrink-0`}>Mode</span>
                                        <select
                                            value={layoutMode}
                                            onChange={(e) => setLayoutMode(e.target.value)}
                                            className={`flex-1 text-xs font-bold p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors`}
                                        >
                                            <option value="auto">Auto (detect screen)</option>
                                            <option value="desktop">Force Desktop</option>
                                            <option value="mobile">Force Mobile</option>
                                        </select>
                                    </div>
                                    {layoutMode === 'auto' && (
                                        <div className="flex items-center gap-2">
                                            <span className={`text-[10px] font-bold ${theme.textMuted} w-10 shrink-0`}>Width</span>
                                            <input
                                                type="number"
                                                value={mobileBreakpoint}
                                                onChange={(e) => setMobileBreakpoint(parseInt(e.target.value) || 768)}
                                                min="320"
                                                max="1440"
                                                className={`flex-1 text-xs font-bold p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors min-w-0`}
                                                title="Screen width below which mobile mode activates"
                                            />
                                            <span className={`text-[10px] ${theme.textMuted}`}>px</span>
                                        </div>
                                    )}
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={showHeaderControlsOnMobile}
                                            onChange={(e) => setShowHeaderControlsOnMobile(e.target.checked)}
                                            className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                        />
                                        <span className={`text-[10px] font-bold ${theme.textMuted}`}>Show header controls on mobile</span>
                                    </label>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Reading Stats */}
                {hasDocument && (
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
                {hasDocument && (
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
                    <div className={`flex-1 overflow-y-auto p-2 space-y-1 ${darkMode ? 'bg-slate-800/30' : 'bg-slate-50/30'} custom-scrollbar`}>
                        <h3 className={`px-3 py-2 text-[10px] font-black ${theme.textMuted} uppercase tracking-widest`}>Page Contents</h3>
                        {textItems.length === 0 && <p className={`text-xs ${theme.textMuted} p-3 italic`}>Upload a PDF or TXT file to see text segments...</p>}
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
                                    if (item.dest) {
                                        try {
                                            let pageIndex;
                                            if (typeof item.dest === 'string') {
                                                const dest = await pdfDoc.getDestination(item.dest);
                                                if (dest) {
                                                    const ref = dest[0];
                                                    pageIndex = await pdfDoc.getPageIndex(ref);
                                                }
                                            } else if (Array.isArray(item.dest)) {
                                                const ref = item.dest[0];
                                                pageIndex = await pdfDoc.getPageIndex(ref);
                                            }
                                            if (pageIndex !== undefined) {
                                                // These will be handled by parent through callbacks
                                                handleChapterNavigation(pageIndex + 1, item.title);
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
    );
}
