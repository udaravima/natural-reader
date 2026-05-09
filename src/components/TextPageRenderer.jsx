/**
 * Renders a pseudo-page of plain text as scrollable sentence spans.
 * Used in place of the PDF canvas when the active document is a .txt file.
 * - Highlights the active sentence (driven by currentSentenceIndex).
 * - Supports native text selection so the existing "Read selection" flow works.
 * - `scale` is reused as a font-size multiplier (PDF zoom controls map to text size).
 */
export default function TextPageRenderer({
    theme,
    darkMode,
    sentences,
    currentSentenceIndex,
    scale,
    effectiveIsMobile,
}) {
    const fontSize = Math.round(14 * scale);

    return (
        <div
            className={`${darkMode ? 'bg-slate-900' : 'bg-white'} ${theme.text} px-6 md:px-12 py-8 md:py-12 leading-relaxed`}
            style={{
                fontSize: `${fontSize}px`,
                width: effectiveIsMobile ? '100%' : 'min(820px, 80vw)',
                minHeight: '60vh',
            }}
        >
            {sentences.length === 0 ? (
                <p className={`${theme.textMuted} italic text-center mt-8`}>
                    (Empty page)
                </p>
            ) : (
                <div className="space-y-1">
                    {sentences.map((text, i) => (
                        <span
                            key={i}
                            className={`inline transition-colors duration-150 rounded-sm px-0.5 ${
                                currentSentenceIndex === i
                                    ? darkMode
                                        ? 'bg-blue-500/30 text-white'
                                        : 'bg-blue-200/80 text-slate-900'
                                    : ''
                            }`}
                        >
                            {text}{' '}
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
}
