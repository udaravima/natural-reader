import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Loader2 } from 'lucide-react';
import { buildApiUrl } from '../utils/url';

/**
 * Render docling-converted Markdown for a document, organized by page.
 *
 * The backend's `GET /v1/docs/{id}/markdown` returns the whole document with
 * `<!-- page N -->` separators; we fetch once, split into per-page slices, and
 * render them in order with anchors so `currentPage` from the PDF toolbar
 * scrolls to the matching page heading.
 */
export default function MarkdownReader({
    theme,
    darkMode,
    apiHost,
    apiPort,
    docId,
    currentPage,
    setCurrentPage,
    scale,
    effectiveIsMobile,
}) {
    const [loading, setLoading] = useState(true);
    const [pages, setPages] = useState([]); // [{ page, markdown }]
    const [error, setError] = useState(null);
    const containerRef = useRef(null);
    // Timestamp of the last programmatic scroll-to-page. The IntersectionObserver
    // below ignores any visibility change in the brief window after — otherwise
    // the auto-scroll triggered by a page-nav click immediately fires the
    // observer, which sets `currentPage` to whatever's mid-flight on screen,
    // which cancels the user's request. The two effectively fight each other.
    const lastProgScrollRef = useRef(0);
    const PROG_SCROLL_QUIET_MS = 800;

    useEffect(() => {
        let cancelled = false;
        if (!docId) return undefined;
        setLoading(true);
        setError(null);
        (async () => {
            try {
                const res = await fetch(
                    buildApiUrl(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}/markdown`),
                );
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const text = await res.text();
                if (cancelled) return;
                setPages(splitMarkdownByPages(text));
            } catch (e) {
                if (!cancelled) setError(e.message || 'Could not load markdown.');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [docId, apiHost, apiPort]);

    // When the PDF toolbar's currentPage changes, scroll to the matching anchor.
    // scrollIntoView walks up to the nearest scrollable ancestor — with no
    // overflow on our own container that's the PdfViewer's outer scroller.
    useEffect(() => {
        if (loading || !pages.length || !containerRef.current) return;
        const el = containerRef.current.querySelector(`[data-md-page="${currentPage}"]`);
        if (el) {
            lastProgScrollRef.current = Date.now();
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }, [currentPage, loading, pages.length]);

    // When the user scrolls a different page into view, update currentPage so
    // the toolbar input + chat-context features keep tracking position.
    // Observes against the viewport (root: null) since we no longer maintain
    // an inner scroll container.
    useEffect(() => {
        if (loading || !pages.length || !containerRef.current || !setCurrentPage) return undefined;
        const container = containerRef.current;
        const observer = new IntersectionObserver(
            (entries) => {
                // Suppress observer updates briefly after a programmatic scroll
                // so a click on "next page" isn't overridden by the in-flight
                // intersection events it triggers.
                if (Date.now() - lastProgScrollRef.current < PROG_SCROLL_QUIET_MS) return;
                const visible = entries
                    .filter((e) => e.isIntersecting)
                    .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
                if (visible) {
                    const p = parseInt(visible.target.dataset.mdPage, 10);
                    if (p && p !== currentPage) setCurrentPage(p);
                }
            },
            { root: null, threshold: 0.1, rootMargin: '-30% 0px -60% 0px' },
        );
        container.querySelectorAll('[data-md-page]').forEach((el) => observer.observe(el));
        return () => observer.disconnect();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [loading, pages.length]);

    const fontSize = Math.round(15 * scale);
    const components = useMemo(() => buildMarkdownComponents(darkMode), [darkMode]);

    const panelBg = darkMode ? 'bg-slate-900' : 'bg-white';
    const sepBg = darkMode ? 'bg-slate-700/40' : 'bg-slate-200';
    const sepText = darkMode ? 'text-slate-400' : 'text-slate-500';

    return (
        // No `overflow` here — we want the PdfViewer's outer scroller to
        // handle scroll so page-nav, zoom, and the toolbar all behave the same
        // way they do for the PDF canvas. Width tracks the user's viewport so
        // MD prose (which reflows naturally) gets a wide reading column on
        // desktop without overflowing on mobile.
        <div
            ref={containerRef}
            className={`${panelBg} ${theme.text}`}
            style={{
                width: effectiveIsMobile ? '100%' : 'min(1200px, 95vw)',
            }}
        >
            {loading && (
                <div className={`flex items-center gap-2 px-6 py-4 ${theme.textMuted} text-sm`}>
                    <Loader2 size={14} className="animate-spin" />
                    Loading converted text…
                </div>
            )}
            {error && (
                <div className="px-6 py-4 text-sm text-red-500">
                    Could not load Markdown: {error}
                </div>
            )}
            {!loading && !error && pages.map(({ page, markdown }) => (
                <div key={page} data-md-page={page} className="px-6 md:px-12 py-6 leading-relaxed" style={{ fontSize: `${fontSize}px` }}>
                    <div className={`flex items-center gap-3 mb-3 ${sepText}`}>
                        <div className={`flex-1 h-px ${sepBg}`} />
                        <span className="text-[11px] uppercase tracking-wider font-bold">Page {page}</span>
                        <div className={`flex-1 h-px ${sepBg}`} />
                    </div>
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                        {markdown}
                    </ReactMarkdown>
                </div>
            ))}
        </div>
    );
}

// Backend joins pages with `<!-- page N -->` markers. We split on those so we
// can render each page in its own anchored section.
function splitMarkdownByPages(text) {
    if (!text) return [];
    const matches = Array.from(text.matchAll(/<!--\s*page\s+(\d+)\s*-->/gi));
    if (matches.length === 0) {
        return [{ page: 1, markdown: text.trim() }].filter((p) => p.markdown);
    }
    const out = [];
    for (let i = 0; i < matches.length; i++) {
        const m = matches[i];
        const page = parseInt(m[1], 10);
        const start = m.index + m[0].length;
        const end = i + 1 < matches.length ? matches[i + 1].index : text.length;
        const slice = text.slice(start, end).trim();
        if (slice) out.push({ page, markdown: slice });
    }
    return out;
}

function buildMarkdownComponents(darkMode) {
    const codeBlockClass = darkMode
        ? 'bg-slate-800/80 text-slate-100'
        : 'bg-slate-900/90 text-slate-100';
    const codeInline = darkMode ? 'bg-slate-700/60' : 'bg-slate-200';
    return {
        p: (props) => <p {...props} className="my-2" />,
        h1: (props) => <h1 {...props} className="text-3xl font-bold mt-5 mb-3" />,
        h2: (props) => <h2 {...props} className="text-2xl font-bold mt-4 mb-2" />,
        h3: (props) => <h3 {...props} className="text-xl font-semibold mt-3 mb-2" />,
        h4: (props) => <h4 {...props} className="text-lg font-semibold mt-3 mb-1.5" />,
        ul: (props) => <ul {...props} className="list-disc pl-7 my-2" />,
        ol: (props) => <ol {...props} className="list-decimal pl-7 my-2" />,
        li: (props) => <li {...props} className="my-1" />,
        a: ({ children, href, ...props }) => (
            <a {...props} href={href} target="_blank" rel="noopener noreferrer" className="text-blue-500 underline break-words">
                {children}
            </a>
        ),
        code: ({ inline, children, ...props }) => inline
            ? <code {...props} className={`px-1 py-0.5 rounded text-[0.9em] font-mono ${codeInline}`}>{children}</code>
            : <code {...props} className="font-mono">{children}</code>,
        pre: (props) => <pre {...props} className={`my-3 p-3 rounded-lg overflow-x-auto text-sm font-mono ${codeBlockClass}`} />,
        blockquote: (props) => (
            <blockquote {...props} className={`border-l-4 pl-4 my-3 italic ${darkMode ? 'border-slate-500 text-slate-300' : 'border-slate-400 text-slate-700'}`} />
        ),
        table: ({ children, ...props }) => (
            <div className="overflow-x-auto my-3">
                <table {...props} className="border-collapse text-sm w-full">{children}</table>
            </div>
        ),
        thead: ({ children, ...props }) => (
            <thead {...props} className={darkMode ? 'bg-slate-800/60' : 'bg-slate-100'}>{children}</thead>
        ),
        th: (props) => <th {...props} className="border border-slate-500/40 px-3 py-1.5 font-semibold text-left" />,
        td: (props) => <td {...props} className="border border-slate-500/40 px-3 py-1.5" />,
        hr: (props) => <hr {...props} className={`my-6 ${darkMode ? 'border-slate-700' : 'border-slate-300'}`} />,
        img: ({ alt, src, ...props }) => <img {...props} alt={alt} src={src} className="max-w-full rounded-md my-3" />,
    };
}
