/**
 * Convert a markdown fragment into TTS-friendly plain text.
 * Keeps visible text + light punctuation; drops markup that would otherwise be
 * vocalized as "star star" / "hash" / "underscore underscore".
 *
 * Used by useChatEngine.js before enqueuing assistant text to the TTS engine.
 * The reader path (PDF / .txt sentences) is plain prose already and skips this.
 */
export function markdownToSpeech(md) {
    if (!md) return '';
    let s = md;
    // Drop fenced code blocks entirely — code doesn't read aloud well
    s = s.replace(/```[\s\S]*?```/g, ' ');
    // Inline code → keep the inner text
    s = s.replace(/`([^`]+)`/g, '$1');
    // Images ![alt](url) → keep alt text only
    s = s.replace(/!\[([^\]]*)]\([^)]*\)/g, '$1');
    // Links [text](url) → keep visible text only
    s = s.replace(/\[([^\]]+)]\([^)]*\)/g, '$1');
    // Bold / italic / strikethrough — keep inner text
    s = s.replace(/(\*\*|__)(.*?)\1/g, '$2');
    s = s.replace(/(\*|_)(.*?)\1/g, '$2');
    s = s.replace(/~~(.*?)~~/g, '$1');
    // Heading hashes → strip leading #s, append ":" so TTS pauses naturally
    s = s.replace(/^\s{0,3}(#{1,6})\s+(.+)$/gm, '$2:');
    // Blockquote markers
    s = s.replace(/^\s{0,3}>\s?/gm, '');
    // List bullets and numeric prefixes → drop marker, keep item text
    s = s.replace(/^\s*[-*+]\s+/gm, '');
    s = s.replace(/^\s*\d+\.\s+/gm, '');
    // Horizontal rules
    s = s.replace(/^\s*([-*_])\1{2,}\s*$/gm, '');
    // Stray dangling markers left over from partial-stream fragments — best effort.
    // Only consume single leftover *, _, # sequences that no longer have a partner.
    s = s.replace(/(^|\s)([*_~`#]{1,3})(\s|$)/g, '$1$3');
    // Collapse whitespace
    s = s.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    return s;
}
