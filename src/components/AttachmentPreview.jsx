import { Image as ImageIcon, FileAudio, X } from 'lucide-react';
import { hasAttachmentData, formatAttachmentSize } from '../utils/attachment';

/**
 * Renders an attachment as a compact chip.
 *
 * Two modes (decided by the attachment shape):
 *   - Live (has base64/dataUrl): shows the actual image thumbnail or an
 *     <audio controls> element, so the user can preview before sending.
 *   - Placeholder (metadata only — loaded from a saved session): shows a
 *     muted icon + name + size chip. Hovering explains why.
 *
 * If `onRemove` is provided, an X button is rendered (used in the pending bar
 * but not inside saved message bubbles).
 */
export default function AttachmentPreview({ attachment, theme, darkMode, onRemove }) {
    if (!attachment) return null;
    const isLive = hasAttachmentData(attachment);
    const isImage = attachment.kind === 'image';

    if (isLive && isImage) {
        return (
            <div className={`relative inline-block rounded-lg overflow-hidden border ${theme.border} ${theme.bgTertiary} group`}>
                <img
                    src={attachment.dataUrl}
                    alt={attachment.name}
                    className="block max-h-32 max-w-[200px] object-cover"
                />
                <div className={`px-2 py-0.5 text-[9px] ${theme.textMuted} truncate max-w-[200px]`} title={attachment.name}>
                    {attachment.name} · {formatAttachmentSize(attachment.size)}
                </div>
                {onRemove && (
                    <button
                        onClick={onRemove}
                        className={`absolute top-1 right-1 p-1 rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500`}
                        title="Remove attachment"
                    >
                        <X size={10} />
                    </button>
                )}
            </div>
        );
    }

    if (isLive && !isImage) {
        return (
            <div className={`inline-flex flex-col gap-1 px-2 py-1.5 rounded-lg border ${theme.border} ${theme.bgTertiary} max-w-[260px]`}>
                <div className="flex items-center gap-2">
                    <FileAudio size={12} className={theme.textMuted} />
                    <span className={`text-[10px] font-bold ${theme.textSecondary} truncate min-w-0 flex-1`} title={attachment.name}>
                        {attachment.name}
                    </span>
                    <span className={`text-[9px] ${theme.textMuted} shrink-0`}>{formatAttachmentSize(attachment.size)}</span>
                    {onRemove && (
                        <button
                            onClick={onRemove}
                            className={`p-0.5 rounded ${theme.hover} ${theme.textMuted} hover:text-red-500`}
                            title="Remove attachment"
                        >
                            <X size={11} />
                        </button>
                    )}
                </div>
                <audio controls src={attachment.dataUrl} className="w-full h-7" />
            </div>
        );
    }

    // Placeholder (metadata only, after a saved session is reopened)
    const Icon = isImage ? ImageIcon : FileAudio;
    return (
        <div
            className={`inline-flex items-center gap-2 px-2 py-1 rounded-lg border ${theme.border} ${darkMode ? 'bg-slate-800/40' : 'bg-slate-100'} text-[10px] ${theme.textMuted}`}
            title="Attachment data not stored — only metadata was kept on save"
        >
            <Icon size={11} />
            <span className={`font-bold ${theme.textSecondary} truncate max-w-[160px]`}>{attachment.name}</span>
            <span>·</span>
            <span>{formatAttachmentSize(attachment.size)}</span>
        </div>
    );
}
