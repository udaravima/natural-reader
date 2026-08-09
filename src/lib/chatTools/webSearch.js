/**
 * Stub showing the shape new tools should follow. NOT registered in `index.js`.
 *
 * Adding a new tool — say, web search — is a four-step recipe:
 *
 *   1. Copy this file to e.g. `webSearch.js` and fill in the four fields below.
 *   2. If your tool needs a backend (most do — anything network-dependent),
 *      add the matching endpoint under `server/routers/` and reach it via
 *      `buildApiUrl(ctx.apiHost, ctx.apiPort, '/v1/...')`.
 *   3. Import the file in `src/lib/chatTools/index.js` and add it to REGISTRY.
 *   4. Make sure the `when(ctx)` gate is tight — only advertise the tool when
 *      it can actually run, so models don't get tantalized by tools that 503.
 *
 * The `ctx` object passed in is currently:
 *   { currentDocId, currentDocIndexState, apiHost, apiPort }
 * Extend it in `useChatEngine.js`'s `toolCtx` build if your tool needs more
 * (e.g. user preferences, selected model, etc.). Keep it serializable —
 * tools shouldn't reach into React state directly.
 */

// Uncomment + customize to use:
import { buildApiUrl } from '../../utils/url';

export default {
    name: 'web_search',
    definition: {
        type: 'function',
        function: {
            name: 'web_search',
            description: 'Searches the live internet for real-time information, recent news, current prices, and up-to-date facts. Use this tool whenever a user asks about events after your knowledge cutoff, requests current data/statistics, or asks a factual question requiring live information or verification.',
            parameters: {
                type: 'object',
                properties: {
                    query: { type: 'string', description: 'The optimized search query string used to look up information on the web.' },
                    count: { type: 'integer', minimum: 1, maximum: 15, description: 'Number of results.' },
                },
                required: ['query'],
            },
        },
    },
    when: (_ctx) => true, // tighten: e.g. gate on a user preference
    execute: async (args, ctx) => {
        const res = await fetch(
            buildApiUrl(ctx.apiHost, ctx.apiPort, '/v1/tools/web_search'),
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(args) },
        );
        const query = args?.query || '';
        const count = args?.count || 5;
        if (!query) return { error: 'query is required and must be non-empty.' };
        if (count < 1 || count > 15) return { error: 'count must be between 1 and 15.' };
        if (!res.ok) return { error: `Web search HTTP ${res.status}` };
        const data = await res.json();
        return { 
            summary_text: `Web search for "${query}" returned ${data?.results?.length || 0} result(s).`,
            agent_response: data 
        };
    },
};
