/**
 * Helpers for image, audio and text-file attachments on chat messages.
 *
 * Lifecycle:
 *   1. ChatView captures a File (paperclip / drop / paste) or Blob (mic).
 *   2. fileToAttachment() reads it as base64 + dataUrl into an Attachment.
 *   3. ChatView stages it in pendingAttachments; on send, it's attached to the
 *      user message in memory and base64-encoded in the Ollama POST body.
 *   4. saveActiveSession() pipes each through stripAttachmentData() before
 *      writing to IndexedDB so we don't bloat sessions with binary blobs.
 *      Reloaded sessions render placeholder chips instead of the actual data.
 *
 * Text files (.env, .json, ...) are a third kind: ChatView stages them with
 * both their original and their masked text (see textAttachment.js); the chat
 * engine sends one of the two and keeps only metadata in the message.
 */

import { textToBase64 } from './textAttachment';

export const MAX_IMAGE_BYTES = 10 * 1024 * 1024; // 10 MB
export const MAX_AUDIO_BYTES = 25 * 1024 * 1024; // 25 MB

export const IMAGE_ACCEPT = 'image/*';
// Mirrors textAttachment.js's extension list, so the picker offers what
// isTextFile() will take. isTextFile takes any name starting .env, but an
// accept entry only matches a name's end, so the common siblings are listed. `.env` files often have no MIME type at all.
export const TEXT_ACCEPT = 'text/*,.txt,.md,.env,.json,.yaml,.yml,.toml,.ini,.conf,.cfg,.csv,.tsv,.log,.xml,.html,.css,.js,.ts,.jsx,.tsx,.py,.sh,.sql,.properties,.env.local,.env.development,.env.production,.env.staging,.env.test,.env.example';

// What a staged text file sends: its real text only after the person chose to.
export const sentTextOf = (att) => (att.sendReal ? att.originalText : att.maskedText);
export const utf8Bytes = (text) => new TextEncoder().encode(text || '').length;

// What could leave the browser for a text file: the larger of its two forms,
// since Send real values can swap one for the other after staging.
export const textLimitBytes = (att) => Math.max(utf8Bytes(att.originalText), utf8Bytes(att.maskedText));

// A staged text file as the request carries it (the only place its text is
// encoded) and as the message keeps it (metadata only: no text, no base64).
export const textAttachmentWire = (att) => {
    const text = sentTextOf(att);
    const masked = !att.sendReal && att.masked > 0;
    // The person's choice to send real values, recorded so the server keeps those values with the model they
    // were agreed for. Not the same as `!masked`: that is also true when there was nothing to mask.
    const real_values = !!att.sendReal && att.masked > 0;
    const size = utf8Bytes(text);
    return {
        wire: { id: att.id, kind: 'text', mime: 'text/plain', name: att.name || '', size, masked, real_values, base64: textToBase64(text) },
        meta: { id: att.id, kind: 'text', name: att.name, mimeType: 'text/plain', size, masked, real_values },
    };
};

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
    ...(typeof att.masked === 'boolean' ? { masked: att.masked } : {}),
    ...(att.real_values === true ? { real_values: true } : {}),
});

// True when the attachment still carries its full binary payload (in-memory).
export const hasAttachmentData = (att) => !!(att && att.base64 && att.dataUrl);
