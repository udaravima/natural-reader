/**
 * `search_document` — semantic search over the currently-loaded, indexed doc.
 *
 * Gated by `when`: only advertised when there's a hash for the open doc AND
 * the backend reports its state as 'indexed'. When inactive (no doc loaded,
 * or chunks uploaded but not yet embedded), the model sees no tool and
 * answers from training only.
 *
 * On invocation: POST /v1/docs/{doc_id}/search with the model-chosen query
 * and k (capped 1-10). Returns trimmed results — page, score, and a text
 * preview capped per-chunk so the follow-up Ollama request stays bounded.
 */
import { buildApiUrl } from '../../utils/url';

const PER_CHUNK_TEXT_CAP = 1500;

export default {
    name: 'search_document',
    definition: {
        type: 'function',
        function: {
            name: 'search_document',
            description:
                "Search the document the user is currently reading for passages relevant to a question. " +
                "Use this when the user's question is about the document's contents and you don't already " +
                "have the relevant excerpt in your context. Returns up to k chunks ranked by semantic " +
                "similarity, each tagged with its page number so you can cite it.",
            parameters: {
                type: 'object',
                properties: {
                    query: {
                        type: 'string',
                        description:
                            "Natural-language search query. Be specific — concepts and phrasings from " +
                            "the user's question work better than single keywords.",
                    },
                    k: {
                        type: 'integer',
                        description: 'How many top chunks to return (1-10). Default 5.',
                        minimum: 1,
                        maximum: 10,
                    },
                },
                required: ['query'],
            },
        },
    },
    when: (ctx) => !!ctx?.currentDocId && ctx?.currentDocIndexState === 'indexed',
    execute: async (args, ctx) => {
        if (!ctx?.currentDocId) return { error: 'No document is currently loaded.' };
        const query = (args?.query || '').trim();
        if (!query) return { error: 'query is required and must be non-empty.' };
        const k = Math.max(1, Math.min(10, Number.isFinite(args?.k) ? Math.floor(args.k) : 5));

        const url = buildApiUrl(ctx.apiHost, ctx.apiPort, `/v1/docs/${encodeURIComponent(ctx.currentDocId)}/search`);
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, k }),
        });
        if (!res.ok) {
            return { error: `Search backend returned HTTP ${res.status}` };
        }
        const data = await res.json();
        const results = Array.isArray(data?.results) ? data.results : [];
        return {
            query,
            chunk_count: results.length,
            results: results.map((r, i) => ({
                index: i + 1,
                page: r.page ?? null,
                score: typeof r.score === 'number' ? Number(r.score.toFixed(4)) : null,
                text: typeof r.text === 'string' && r.text.length > PER_CHUNK_TEXT_CAP
                    ? r.text.slice(0, PER_CHUNK_TEXT_CAP) + ' [truncated]'
                    : r.text,
            })),
        };
    },
};
