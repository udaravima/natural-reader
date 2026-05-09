/**
 * Helpers for image/audio attachments on chat messages.
 *
 * Lifecycle:
 *   1. ChatView captures a File (paperclip / drop / paste) or Blob (mic).
 *   2. fileToAttachment() reads it as base64 + dataUrl into an Attachment.
 *   3. ChatView stages it in pendingAttachments; on send, it's attached to the
 *      user message in memory and base64-encoded in the Ollama POST body.
 *   4. saveActiveSession() pipes each through stripAttachmentData() before
 *      writing to IndexedDB so we don't bloat sessions with binary blobs.
 *      Reloaded sessions render placeholder chips instead of the actual data.
 */

export const MAX_IMAGE_BYTES = 10 * 1024 * 1024; // 10 MB
export const MAX_AUDIO_BYTES = 25 * 1024 * 1024; // 25 MB

export const IMAGE_ACCEPT = 'image/*';
export const AUDIO_ACCEPT = 'audio/*';
export const ATTACHMENT_ACCEPT = `${IMAGE_ACCEPT},${AUDIO_ACCEPT}`;

export const kindFromMime = (mime) => {
    if (!mime) return null;
    if (mime.startsWith('image/')) return 'image';
    if (mime.startsWith('audio/')) return 'audio';
    return null;
};

const newAttachmentId = () =>
    (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const formatBytesShort = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export const formatAttachmentSize = formatBytesShort;

// Read a File or Blob into a complete Attachment. Returns { ok, attachment } or
// { ok: false, error } so callers can show user-friendly toast messages.
export const fileToAttachment = (file, { fallbackName } = {}) =>
    new Promise((resolve) => {
        const mime = file.type || 'application/octet-stream';
        const kind = kindFromMime(mime);
        if (!kind) {
            resolve({ ok: false, error: `Unsupported file type: ${mime || 'unknown'}` });
            return;
        }
        const max = kind === 'image' ? MAX_IMAGE_BYTES : MAX_AUDIO_BYTES;
        if (file.size > max) {
            resolve({
                ok: false,
                error: `${kind === 'image' ? 'Image' : 'Audio'} too large (max ${formatBytesShort(max)})`,
            });
            return;
        }
        const name = file.name || fallbackName || (kind === 'image' ? 'image' : 'recording');
        const reader = new FileReader();
        reader.onload = () => {
            const dataUrl = reader.result;
            // dataUrl format: data:<mime>;base64,<payload>
            const base64 = typeof dataUrl === 'string' ? dataUrl.split(',', 2)[1] || '' : '';
            resolve({
                ok: true,
                attachment: {
                    id: newAttachmentId(),
                    kind,
                    name,
                    mimeType: mime,
                    size: file.size,
                    dataUrl,
                    base64,
                },
            });
        };
        reader.onerror = () => resolve({ ok: false, error: 'Failed to read file' });
        reader.readAsDataURL(file);
    });

// Strip the heavy binary fields before persisting to IndexedDB. The placeholder
// metadata is enough to render a chip on reopen.
export const stripAttachmentData = (att) => ({
    id: att.id,
    kind: att.kind,
    name: att.name,
    mimeType: att.mimeType,
    size: att.size,
});

// True when the attachment still carries its full binary payload (in-memory).
export const hasAttachmentData = (att) => !!(att && att.base64 && att.dataUrl);
