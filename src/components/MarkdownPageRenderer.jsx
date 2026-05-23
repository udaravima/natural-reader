import { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Renders a paginated chunk of markdown via react-markdown + remark-gfm.
 * Mirrors TextPageRenderer's container styling but highlights at the *block*
 * (paragraph / list / heading / table) level — `paragraphMap[i]` maps the
 * currently-spoken sentence to the block it belongs to.
 *
 * Highlight alignment uses each rendered element's `node.position.start.offset`
 * (passed by react-markdown when `passNode` is on, which is the default) and
 * looks it up in `pageData.blockOffsets`. This works regardless of nesting
 * depth — an inner `<ul>` inside a list item resolves to its enclosing
 * top-level list block, not a separate one.
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

    const findBlockIndex = (offset) => {
        const ranges = pageData?.blockOffsets;
        if (!ranges || offset == null) return -1;
        for (let i = 0; i < ranges.length; i++) {
            const [s, e] = ranges[i];
            if (offset >= s && offset < e) return i;
        }
        return -1;
    };

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

    // For top-level block-level elements, attach the block-index resolved from
    // node offset and highlight if it's the active block. Inner/nested
    // invocations still resolve to their enclosing block (so highlight is
    // consistent), but to avoid duplicating the data attribute on inner
    // elements we only attach it when the node's start matches the block start.
    const wrapBlock = (Tag, baseClass = '') => {
        const Component = ({ children, node, ...props }) => {
            const offset = node?.position?.start?.offset;
            const i = findBlockIndex(offset);
            const isActive = i >= 0 && i === activeBlock;
            const isTopLevel = i >= 0 && pageData.blockOffsets[i][0] === offset;
            return (
                <Tag
                    {...props}
                    data-block-index={isTopLevel ? i : undefined}
                    className={`${baseClass} rounded-md transition-colors duration-200 ${isTopLevel ? highlightFor(isActive) : ''}`}
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
        pre: ({ children, node, ...props }) => {
            const offset = node?.position?.start?.offset;
            const i = findBlockIndex(offset);
            const isTopLevel = i >= 0 && pageData.blockOffsets[i][0] === offset;
            return (
                <pre
                    {...props}
                    data-block-index={isTopLevel ? i : undefined}
                    className={`my-3 p-3 rounded-lg overflow-x-auto text-sm font-mono ${darkMode ? 'bg-slate-800/80 text-slate-100' : 'bg-slate-900/90 text-slate-100'}`}
                >
                    {children}
                </pre>
            );
        },
        blockquote: ({ children, node, ...props }) => {
            const offset = node?.position?.start?.offset;
            const i = findBlockIndex(offset);
            const isActive = i >= 0 && i === activeBlock;
            const isTopLevel = i >= 0 && pageData.blockOffsets[i][0] === offset;
            return (
                <blockquote
                    {...props}
                    data-block-index={isTopLevel ? i : undefined}
                    className={`border-l-4 pl-4 my-3 italic rounded-r-md transition-colors duration-200 ${darkMode ? 'border-slate-500 text-slate-300' : 'border-slate-400 text-slate-700'} ${isTopLevel ? highlightFor(isActive) : ''}`}
                >
                    {children}
                </blockquote>
            );
        },
        table: ({ children, node, ...props }) => {
            const offset = node?.position?.start?.offset;
            const i = findBlockIndex(offset);
            const isActive = i >= 0 && i === activeBlock;
            const isTopLevel = i >= 0 && pageData.blockOffsets[i][0] === offset;
            return (
                <div
                    data-block-index={isTopLevel ? i : undefined}
                    className={`overflow-x-auto my-3 rounded-md transition-colors duration-200 ${isTopLevel ? highlightFor(isActive) : ''}`}
                >
                    <table {...props} className="border-collapse text-sm w-full">{children}</table>
                </div>
            );
        },
        thead: ({ children, ...props }) => <thead {...props} className={darkMode ? 'bg-slate-800/60' : 'bg-slate-100'}>{children}</thead>,
        th: ({ children, ...props }) => <th {...props} className="border border-slate-500/40 px-3 py-1.5 font-semibold text-left">{children}</th>,
        td: ({ children, ...props }) => <td {...props} className="border border-slate-500/40 px-3 py-1.5">{children}</td>,
        hr: ({ ...props }) => (
            <hr {...props} className={`my-6 ${darkMode ? 'border-slate-700' : 'border-slate-300'}`} />
        ),
        img: ({ alt, src, ...props }) => (
            <img {...props} alt={alt} src={src} className="max-w-full rounded-md my-3" />
        ),
    };

    if (!pageData || (pageData.blocks?.length ?? 0) === 0) {
        return (
            <div
                className={`${darkMode ? 'bg-slate-900' : 'bg-white'} ${theme.text} px-6 md:px-12 py-8 md:py-12 leading-relaxed`}
                style={{
                    width: effectiveIsMobile ? '100%' : 'min(1200px, 95vw)',
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
                width: effectiveIsMobile ? '100%' : 'min(1200px, 95vw)',
                minHeight: '60vh',
            }}
        >
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                {pageData.rawMarkdown}
            </ReactMarkdown>
        </div>
    );
}
