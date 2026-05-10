import { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Renders a paginated chunk of markdown via react-markdown + remark-gfm.
 * Mirrors TextPageRenderer's container styling but highlights at the *block*
 * (paragraph / list / heading / table) level — `paragraphMap[i]` maps the
 * currently-spoken sentence to the block it belongs to.
 *
 * Block index is assigned by walking react-markdown's component invocations
 * in source order via a render-scoped mutable counter that the wrapped block
 * components close over. Non-block components (li, td, code) do not increment.
 */
export default function MarkdownPageRenderer({
    theme,
    darkMode,
    pageData,
    currentSentenceIndex,
    scale,
    effectiveIsMobile,
}) {
    const fontSize = Math.round(15 * scale);
    const activeBlock = pageData?.paragraphMap?.[currentSentenceIndex] ?? -1;
    const containerRef = useRef(null);

    // Per-render counter shared by all wrapped block components. React-markdown
    // invokes them depth-first in source order, so the counter naturally aligns
    // with `paragraphMap` block indices computed during pagination.
    const counter = { value: 0 };

    // Auto-scroll the active block into view when it changes (mirrors usePdfEngine behavior).
    useEffect(() => {
        if (activeBlock < 0 || !containerRef.current) return;
        const el = containerRef.current.querySelector(`[data-block-index="${activeBlock}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, [activeBlock]);

    const highlightFor = (isActive) => {
        if (!isActive) return '';
        return darkMode
            ? 'bg-blue-500/20 ring-1 ring-blue-500/30'
            : 'bg-blue-200/50 ring-1 ring-blue-300/60';
    };

    const wrapBlock = (Tag, baseClass = '') => {
        const Component = ({ children, ...props }) => {
            const i = counter.value++;
            const isActive = i === activeBlock;
            return (
                <Tag
                    {...props}
                    data-block-index={i}
                    className={`${baseClass} rounded-md transition-colors duration-200 ${highlightFor(isActive)}`}
                >
                    {children}
                </Tag>
            );
        };
        return Component;
    };

    const components = {
        p: wrapBlock('p', 'my-2 leading-relaxed px-1'),
        h1: wrapBlock('h1', 'text-3xl font-bold mt-5 mb-3 px-1'),
        h2: wrapBlock('h2', 'text-2xl font-bold mt-4 mb-2 px-1'),
        h3: wrapBlock('h3', 'text-xl font-semibold mt-3 mb-2 px-1'),
        h4: wrapBlock('h4', 'text-lg font-semibold mt-3 mb-1.5 px-1'),
        h5: wrapBlock('h5', 'text-base font-semibold mt-2 mb-1 px-1'),
        h6: wrapBlock('h6', 'text-sm font-semibold mt-2 mb-1 px-1 uppercase tracking-wider'),
        ul: wrapBlock('ul', 'list-disc pl-7 my-2 leading-relaxed'),
        ol: wrapBlock('ol', 'list-decimal pl-7 my-2 leading-relaxed'),
        li: ({ children, ...props }) => <li {...props} className="my-1">{children}</li>,
        a: ({ children, href, ...props }) => (
            <a {...props} href={href} target="_blank" rel="noopener noreferrer" className="text-blue-500 underline break-words">
                {children}
            </a>
        ),
        code: ({ inline, children, ...props }) => inline
            ? <code {...props} className={`px-1 py-0.5 rounded text-[0.9em] font-mono ${darkMode ? 'bg-slate-700/60' : 'bg-slate-200'}`}>{children}</code>
            : <code {...props} className="font-mono">{children}</code>,
        pre: ({ children, ...props }) => {
            // Code blocks count toward block index (they appear in the rendered tree)
            // but contribute zero TTS sentences, so they never become activeBlock.
            const i = counter.value++;
            return (
                <pre
                    {...props}
                    data-block-index={i}
                    className={`my-3 p-3 rounded-lg overflow-x-auto text-sm font-mono ${darkMode ? 'bg-slate-800/80 text-slate-100' : 'bg-slate-900/90 text-slate-100'}`}
                >
                    {children}
                </pre>
            );
        },
        blockquote: ({ children, ...props }) => {
            const i = counter.value++;
            const isActive = i === activeBlock;
            return (
                <blockquote
                    {...props}
                    data-block-index={i}
                    className={`border-l-4 pl-4 my-3 italic rounded-r-md transition-colors duration-200 ${darkMode ? 'border-slate-500 text-slate-300' : 'border-slate-400 text-slate-700'} ${highlightFor(isActive)}`}
                >
                    {children}
                </blockquote>
            );
        },
        table: ({ children, ...props }) => {
            const i = counter.value++;
            const isActive = i === activeBlock;
            return (
                <div
                    data-block-index={i}
                    className={`overflow-x-auto my-3 rounded-md transition-colors duration-200 ${highlightFor(isActive)}`}
                >
                    <table {...props} className="border-collapse text-sm w-full">{children}</table>
                </div>
            );
        },
        thead: ({ children, ...props }) => <thead {...props} className={darkMode ? 'bg-slate-800/60' : 'bg-slate-100'}>{children}</thead>,
        th: ({ children, ...props }) => <th {...props} className="border border-slate-500/40 px-3 py-1.5 font-semibold text-left">{children}</th>,
        td: ({ children, ...props }) => <td {...props} className="border border-slate-500/40 px-3 py-1.5">{children}</td>,
        hr: ({ ...props }) => {
            counter.value++;
            return <hr {...props} className={`my-6 ${darkMode ? 'border-slate-700' : 'border-slate-300'}`} />;
        },
        img: ({ alt, src, ...props }) => (
            <img {...props} alt={alt} src={src} className="max-w-full rounded-md my-3" />
        ),
    };

    if (!pageData || (pageData.blocks?.length ?? 0) === 0) {
        return (
            <div
                className={`${darkMode ? 'bg-slate-900' : 'bg-white'} ${theme.text} px-6 md:px-12 py-8 md:py-12 leading-relaxed`}
                style={{
                    width: effectiveIsMobile ? '100%' : 'min(820px, 80vw)',
                    minHeight: '60vh',
                }}
            >
                <p className={`${theme.textMuted} italic text-center mt-8`}>
                    (Empty page)
                </p>
            </div>
        );
    }

    return (
        <div
            ref={containerRef}
            className={`${darkMode ? 'bg-slate-900' : 'bg-white'} ${theme.text} px-6 md:px-12 py-8 md:py-12 leading-relaxed`}
            style={{
                fontSize: `${fontSize}px`,
                width: effectiveIsMobile ? '100%' : 'min(820px, 80vw)',
                minHeight: '60vh',
            }}
        >
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                {pageData.rawMarkdown}
            </ReactMarkdown>
        </div>
    );
}
