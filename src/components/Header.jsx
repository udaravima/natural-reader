import {
    Play, Pause, Square, Upload, Volume2, SkipForward, SkipBack,
    Zap, Loader2, Moon, Sun, Download, Keyboard, Clock,
    PanelLeftClose, Menu
} from 'lucide-react';

export default function Header({
    theme,
    darkMode,
    pdfDoc,
    status,
    isPlaying,
    isLocalhost, setIsLocalhost,
    isDownloading,
    textItems,
    showHeaderControlsOnMobile,
    sidebarOpen, setSidebarOpen,
    showShortcuts, setShowShortcuts,
    fileInputRef,
    // Playback controls
    handlePlayPause,
    stopPlayback,
    skipToNextSentence,
    skipToPrevSentence,
    setDarkMode,
    downloadPageAudio,
    handleFileUpload,
    calculateEstimatedTimeRemaining,
    playbackSpeed,
}) {
    return (
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

            {/* PLAYBACK CONTROLS */}
            <div className={`${showHeaderControlsOnMobile ? 'flex' : 'hidden md:flex'} items-center gap-2 ${theme.bgTertiary} p-1 rounded-xl border ${theme.border} shadow-inner`}>
                <button
                    onClick={skipToPrevSentence}
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
                {pdfDoc && calculateEstimatedTimeRemaining(playbackSpeed) && (
                    <div className={`hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-lg border ${theme.border} text-[10px] font-bold ${theme.textSecondary}`}>
                        <Clock size={12} />
                        {calculateEstimatedTimeRemaining(playbackSpeed)}
                    </div>
                )}

                {/* TTS Mode Toggle */}
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

                {/* Download Page Audio */}
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

                {/* Keyboard Shortcuts */}
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
    );
}
