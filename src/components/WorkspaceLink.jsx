import { useEffect, useState } from 'react';
import { useWorkspace } from '../lib/WorkspaceContext';
import { resolvePath, dirname } from '../utils/resolvePath';
import { isNavigable } from '../lib/workspace';

const LINK_CLASS = 'text-blue-500 underline break-words';

function scrollToAnchor(anchor) {
    if (!anchor) return;
    const el = document.getElementById(anchor);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export function WorkspaceLink({ href, children, ...props }) {
    const ws = useWorkspace();
    const external = (
        <a {...props} href={href} target="_blank" rel="noopener noreferrer" className={LINK_CLASS}>
            {children}
        </a>
    );
    if (!ws?.workspace) return external; // single-file mode: unchanged behavior

    const r = resolvePath(dirname(ws.currentPath || ''), href);
    // Dangerous scheme (javascript:, data:, …) — drop the href entirely,
    // render inert text so it can never be clicked/navigated.
    if (r.kind === 'unsafe') return <span {...props}>{children}</span>;
    if (r.kind === 'external' || r.kind === 'none') return external;

    const onClick = (e) => {
        if (r.kind === 'anchor') {
            e.preventDefault();
            scrollToAnchor(r.anchor);
            return;
        }
        // r.kind === 'path'
        if (ws.workspace.hasFile(r.path) && isNavigable(r.path)) {
            e.preventDefault();
            ws.navigate(r.path, r.anchor);
        } else {
            e.preventDefault();
            ws.onMissing?.(r.path);
        }
    };

    return (
        <a {...props} href={href} onClick={onClick} className={LINK_CLASS}>
            {children}
        </a>
    );
}

export function WorkspaceImage({ src, alt, ...props }) {
    const ws = useWorkspace();
    const [url, setUrl] = useState(null);
    const relative = src && !/^([a-z]+:|\/\/|data:)/i.test(src);

    useEffect(() => {
        let cancelled = false;
        let objectUrl = null;
        if (ws?.workspace && relative) {
            const r = resolvePath(dirname(ws.currentPath || ''), src);
            if (r.kind === 'path' && ws.workspace.hasFile(r.path)) {
                ws.workspace.readBlob(r.path).then((blob) => {
                    if (cancelled) return;
                    objectUrl = URL.createObjectURL(blob);
                    setUrl(objectUrl);
                }).catch(() => { if (!cancelled) setUrl(null); });
            }
        }
        return () => {
            cancelled = true;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [src, ws?.currentPath, ws?.workspace]);

    return <img {...props} alt={alt} src={url || src} className="max-w-full rounded-md my-3" />;
}
