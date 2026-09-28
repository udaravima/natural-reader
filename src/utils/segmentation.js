import { fromMarkdown } from 'mdast-util-from-markdown';
import { gfm } from 'micromark-extension-gfm';
import { gfmFromMarkdown } from 'mdast-util-gfm';
import { toString as mdastToString } from 'mdast-util-to-string';
import { SENTENCES_PER_TEXT_PAGE } from '../constants';

// Shared sentence segmentation: same rule for PDF and .txt so the TTS engine
// receives a consistent textItems shape regardless of source.
export const segmentSentences = (rawText) =>
    rawText
        .replace(/\s+/g, ' ')
        .split(/(?<=[.!?])\s+/)
        .filter(s => s.trim().length > 5);

// Chunk a flat sentence array into pseudo-pages of N sentences.
export const paginateSentences = (sentences, perPage = SENTENCES_PER_TEXT_PAGE) => {
    const pages = [];
    for (let i = 0; i < sentences.length; i += perPage) {
        pages.push(sentences.slice(i, i + perPage));
    }
    return pages.length > 0 ? pages : [[]];
};

// --- MARKDOWN ---
// AST-driven block segmentation: parse with the same micromark/mdast pipeline
// react-markdown uses (CommonMark + GFM), so each top-level mdast child maps
// 1:1 to a block-level component invocation in the renderer. This is what
// drives `paragraphMap` (sentence index → block index) — any mismatch here
// would slide the highlight to the wrong block.
//
// Block carries:
//   raw       — exact source slice (what react-markdown re-parses + renders)
//   plain     — stripped text for sentence extraction / TTS
//   sentences — TTS sentence array (empty for code blocks: skipped by TTS)
//   type      — mdast node type (paragraph, heading, list, code, blockquote, …)
export const splitSentences = (text) =>
    text
        .replace(/\s+/g, ' ')
        .trim()
        .split(/(?<=[.!?])\s+/)
        .map(s => s.trim())
        .filter(s => s.length > 0);

export const segmentMarkdown = (rawText) => {
    if (!rawText) return [];
    const tree = fromMarkdown(rawText, {
        extensions: [gfm()],
        mdastExtensions: [gfmFromMarkdown()],
    });
    const blocks = [];
    for (const node of tree.children) {
        // Skip nodes that don't render visibly — they'd inflate the block count
        // and slide the highlight off by one for every following block.
        if (node.type === 'definition' || node.type === 'footnoteDefinition') continue;
        const start = node.position?.start?.offset ?? 0;
        const end = node.position?.end?.offset ?? rawText.length;
        const raw = rawText.slice(start, end);
        const isCode = node.type === 'code';
        const plain = isCode ? '' : mdastToString(node);
        const sentences = isCode ? [] : splitSentences(plain);
        blocks.push({ raw, plain, sentences, type: node.type });
    }
    return blocks;
};

// Greedy pagination: pack blocks until the next would exceed the soft cap.
// Pages always end on a block boundary; a single oversize block gets its own page.
//
// `blockOffsets[i] = [start, end)` — the byte range of block `i` within the joined
// `rawMarkdown` string. The renderer uses these to map any rendered node (top-level
// OR nested) back to its enclosing block via `node.position.start.offset`.
export const buildMarkdownPage = (blocks) => {
    const sentences = [];
    const paragraphMap = [];
    const blockOffsets = [];
    const parts = [];
    let cursor = 0;
    blocks.forEach((block, blockIndex) => {
        const start = cursor;
        parts.push(block.raw);
        cursor += block.raw.length;
        blockOffsets.push([start, cursor]);
        if (blockIndex < blocks.length - 1) {
            parts.push('\n\n');
            cursor += 2;
        }
        for (const s of block.sentences) {
            sentences.push(s);
            paragraphMap.push(blockIndex);
        }
    });
    return { rawMarkdown: parts.join(''), blocks, sentences, paragraphMap, blockOffsets };
};

export const paginateMarkdownBlocks = (blocks, perPage = SENTENCES_PER_TEXT_PAGE) => {
    const pages = [];
    let current = [];
    let count = 0;
    for (const block of blocks) {
        const size = block.sentences.length;
        if (current.length > 0 && count + size > perPage) {
            pages.push(buildMarkdownPage(current));
            current = [];
            count = 0;
        }
        current.push(block);
        count += size;
    }
    if (current.length > 0) pages.push(buildMarkdownPage(current));
    return pages.length > 0 ? pages : [buildMarkdownPage([])];
};

/**
 * The chunk layout the server must reproduce (server/services/extract.py) so a
 * citation's `page` lands on the page this reader shows. Mirrors the text and
 * markdown branches of usePdfEngine's extractAllChunks exactly — same
 * segmentation, same pagination, same text normalization, same skips.
 */
export function chunkLayout(fileType, rawText) {
    const out = [];
    let ord = 0;
    if (fileType === 'text') {
        const pages = paginateSentences(segmentSentences(rawText), SENTENCES_PER_TEXT_PAGE);
        pages.forEach((sentences, pi) => {
            const text = (sentences || []).join(' ').replace(/\s+/g, ' ').trim();
            if (text) out.push({ ord: ord++, page: pi + 1, chunk_type: 'page', text });
        });
        return out;
    }
    if (fileType === 'markdown') {
        const pages = paginateMarkdownBlocks(segmentMarkdown(rawText), SENTENCES_PER_TEXT_PAGE);
        pages.forEach((pageObj, pi) => {
            for (const block of pageObj.blocks || []) {
                if (block.type === 'code') continue;
                const text = (block.plain || '').replace(/\s+/g, ' ').trim();
                if (text) out.push({ ord: ord++, page: pi + 1, chunk_type: 'block', text });
            }
        });
        return out;
    }
    return out;
}
