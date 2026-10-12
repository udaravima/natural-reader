/**
 * Shown over the reader when the open document is an old version (V1 spec
 * §7): which version, of which document, in which project, and a way to the
 * current one. A strip in normal flow (PdfViewer's `banner` slot, above the
 * toolbar), so it can never cover a control; wraps at phone width.
 */
export default function OldVersionBanner({ old, onOpenCurrent }) {
  if (!old) return null;
  return (
    <div role="status"
      className="shrink-0 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 px-3 py-1.5 text-xs bg-amber-50 text-amber-900 border-b border-amber-300 dark:bg-amber-900/40 dark:text-amber-100 dark:border-amber-700">
      <span>Old version — v{old.version} of {old.current_name} in {old.project.name} (current is v{old.current_version})</span>
      <button type="button" onClick={() => onOpenCurrent(old)} className="underline font-bold">Open current</button>
    </div>
  );
}
