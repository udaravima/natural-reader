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
// import { buildApiUrl } from '../../utils/url';

export default {
    name: 'current_time_date',
    definition: {
        type: 'function',
        function: {
            name: 'current_time_date',
            description: 'Get the current time and date. and locale. Use this tool whenever a user asks about the current time, date, or timezone information.',
            parameters: {
                type: 'object',
                properties: {},
            },
        },
    },
    when: (_ctx) => true,
    execute: async (args, ctx) => {
        const currentDateTimeLocale = new Date().toString();
        return {
            summary_text: currentDateTimeLocale,
            agent_response:{
                current_time_date: currentDateTimeLocale,
            }
        };
    },
};
