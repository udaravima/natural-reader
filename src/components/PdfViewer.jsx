import {
    ChevronLeft, ChevronRight, ZoomIn, ZoomOut,
    RotateCcw, Maximize, Minimize, MessageSquare
} from 'lucide-react';
import WelcomeScreen from './WelcomeScreen';
import TextPageRenderer from './TextPageRenderer';
import MarkdownPageRenderer from './MarkdownPageRenderer';

export default function PdfViewer({
    theme,
    darkMode,
    effectiveIsMobile,
    pdfDoc,
    fileType,
    textItems,
    currentSentenceIndex,
    currentPage, setCurrentPage, goToNextPage, goToPrevPage,
    numPages,
    scale, setScale,
    canvasRef,
    textLayerRef,
    pdfContainerRef,
    fileInputRef,
    recentBooks,
    openFromLibrary,
    removeFromLibrary,
    markdownPageData,
    onAskAboutPage,
}) {
    const isText = fileType === 'text';
    const isMarkdown = fileType === 'markdown';
    const hasDoc = (isText || isMarkdown) ? numPages > 0 : !!pdfDoc;
    return (
        <section className={`flex-1 flex flex-col overflow-hidden ${theme.viewportBg} transition-colors duration-300`}>

            {/* PDF OPTIONS TOOLBAR */}
            {hasDoc && (
                <div className={`flex items-center justify-between px-4 py-2 ${theme.bgSecondary} border-b ${theme.border} shrink-0`}>
                    {/* LEFT: Page Navigation */}
                    <div className="flex items-center gap-2">
                        <button
                            onClick={goToPrevPage}
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
                            onClick={goToNextPage}
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

                    {/* RIGHT: Fit Options + Chat actions */}
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
                        {onAskAboutPage && (
                            <>
                                <div className={`w-px h-6 ${theme.border} mx-1`}></div>
                                <button
                                    onClick={() => onAskAboutPage(currentPage)}
                                    className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} ${theme.textSecondary} hover:text-blue-500`}
                                    title="Ask the model about this page"
                                >
                                    <MessageSquare size={14} className="inline mr-1" />
                                    Ask page
                                </button>
                            </>
                        )}
                    </div>
                </div>
            )}

            {/* DOCUMENT CONTAINER (PDF canvas or text page) */}
            <div
                ref={pdfContainerRef}
                className={`flex-1 overflow-auto p-2 md:p-6 flex justify-center custom-scrollbar ${darkMode ? 'bg-slate-900/50' : 'bg-slate-300/30'} ${effectiveIsMobile && hasDoc ? 'pb-28' : ''}`}
            >
                <div
                    className={`relative ${theme.canvasBg} shadow-2xl rounded-sm border ${theme.border}`}
                    style={{
                        height: 'fit-content',
                        maxWidth: effectiveIsMobile ? '100%' : undefined,
                    }}
                >
                    {hasDoc ? (
                        isMarkdown ? (
                            <MarkdownPageRenderer
                                theme={theme}
                                darkMode={darkMode}
                                pageData={markdownPageData}
                                currentSentenceIndex={currentSentenceIndex}
                                scale={scale}
                                effectiveIsMobile={effectiveIsMobile}
                            />
                        ) : isText ? (
                            <TextPageRenderer
                                theme={theme}
                                darkMode={darkMode}
                                sentences={textItems}
                                currentSentenceIndex={currentSentenceIndex}
                                scale={scale}
                                effectiveIsMobile={effectiveIsMobile}
                            />
                        ) : (
                            <>
                                <canvas
                                    ref={canvasRef}
                                    className="block"
                                    style={{
                                        maxWidth: effectiveIsMobile ? '100%' : undefined,
                                        height: effectiveIsMobile ? 'auto' : undefined,
                                    }}
                                />
                                <div
                                    ref={textLayerRef}
                                    className="textLayer absolute top-0 left-0 overflow-hidden opacity-25 leading-none"
                                    style={{ pointerEvents: 'auto' }}
                                />
                            </>
                        )
                    ) : (
                        <WelcomeScreen
                            theme={theme}
                            fileInputRef={fileInputRef}
                            recentBooks={recentBooks}
                            openFromLibrary={openFromLibrary}
                            removeFromLibrary={removeFromLibrary}
                        />
                    )}
                </div>
            </div>
        </section>
    );
}
