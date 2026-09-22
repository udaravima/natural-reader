import { List, BookOpen } from 'lucide-react';

export default function Sidebar({
    theme,
    darkMode,
    effectiveIsMobile,
    sidebarOpen,
    sidebarTab, setSidebarTab,
    // PDF state
    hasDocument,
    pdfDoc,
    pdfOutline,
    textItems,
    currentSentenceIndex,
    sentenceRefs,
    // Actions
    calculateReadingProgress,
    handleMobileSentenceClick,
    handleSentenceContextMenu,
    handleChapterNavigation,
}) {
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
