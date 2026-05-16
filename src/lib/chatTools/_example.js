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
//
// export default {
//     name: 'web_search',
//     definition: {
//         type: 'function',
//         function: {
//             name: 'web_search',
//             description: 'Search the public web for up-to-date information not in the model\'s training data.',
//             parameters: {
//                 type: 'object',
//                 properties: {
//                     query: { type: 'string', description: 'Search query.' },
//                     count: { type: 'integer', minimum: 1, maximum: 10, description: 'Number of results.' },
//                 },
//                 required: ['query'],
//             },
//         },
//     },
//     when: (_ctx) => true, // tighten: e.g. gate on a user preference
//     execute: async (args, ctx) => {
//         const res = await fetch(
//             buildApiUrl(ctx.apiHost, ctx.apiPort, '/v1/tools/web_search'),
//             { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(args) },
//         );
//         if (!res.ok) return { error: `Web search HTTP ${res.status}` };
//         return await res.json();
//     },
// };
