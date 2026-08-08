/**
 * Chat-tool registry — frontend-orchestrated tool calling for Ollama.
 *
 * Each tool exports a default object with shape:
 *
 *   {
 *     name: 'search_document',           // matches function.name in the JSON-schema definition
 *     definition: { type: 'function', function: { name, description, parameters } },
 *     when: (ctx) => boolean,            // gate: should this tool be advertised right now?
 *     execute: async (args, ctx) => any, // run the tool, return JSON-serializable result
 *   }
 *
 * `ctx` is `{ currentDocId, currentDocIndexState, apiHost, apiPort }` — everything
 * tools need without depending on React state. The engine passes it through on
 * every call.
 *
 * Adding a new tool = drop a file in this folder, import it into REGISTRY below.
 * See `_example.js` for the shape.
 */
import searchDocument from './searchDocument';
import webSearch from './webSearch';
import currentTimeDate from './currentTimeDate';

const REGISTRY = [searchDocument, webSearch, currentTimeDate];

/**
 * Build the `tools: [...]` array to send to Ollama. Tools whose `when(ctx)`
 * returns false are omitted — sending zero-tools means we don't include the
 * `tools` field at all (saves bandwidth and avoids surprising the model with
 * an empty toolbox).
 */
export function getToolDefinitions(ctx) {
    return REGISTRY
        .filter(t => {
            try { return !!t.when?.(ctx); }
            catch { return false; }
        })
        .map(t => t.definition);
}

/**
 * Execute a tool by name. Errors are wrapped (rather than thrown) so the model
 * sees a structured `{ error: '...' }` and can recover — much better UX than
 * the chat layer aborting on a transient backend hiccup.
 */
export async function executeToolCall(name, args, ctx) {
    const tool = REGISTRY.find(t => t.name === name);
    if (!tool) {
        return { error: `Unknown tool: ${name}` };
    }
    try {
        return await tool.execute(args || {}, ctx);
    } catch (e) {
        console.error(`Tool ${name} failed:`, e);
        return { error: String(e?.message || e) };
    }
}

/** Useful for the UI disclosure: turn a registered tool's name into a user-facing label. */
export function describeTool(name) {
    const tool = REGISTRY.find(t => t.name === name);
    return tool?.definition?.function?.description || name;
}
