// Regression guard, not a red-green step. This pins the exact /api/chat body
// shape so a future change to buildRequestFields cannot silently start sending
// keys we promised to omit (see the "unset means omit" constraint). It is green
// on first run by design — it protects a contract rather than driving new code.
import { describe, it, expect } from 'vitest';
import { buildRequestFields, INFERENCE_DEFAULTS } from './inference';

// The body shape useChatEngine sends today, before inference settings existed.
const legacyBody = ({ model, messages, think }) => ({ model, messages, stream: true, think });

const newBody = ({ model, messages, settings }) => ({
    model, messages, stream: true, ...buildRequestFields(settings),
});

describe('/api/chat body construction', () => {
    const model = 'qwen3.5:latest';
    const messages = [{ role: 'user', content: 'hi' }];

    it('is identical to the legacy body when every setting is unset', () => {
        expect(newBody({ model, messages, settings: INFERENCE_DEFAULTS }))
            .toEqual(legacyBody({ model, messages, think: false }));
    });

    it('is identical to the legacy thinking-on body when think is "on"', () => {
        expect(newBody({ model, messages, settings: { ...INFERENCE_DEFAULTS, think: 'on' } }))
            .toEqual(legacyBody({ model, messages, think: true }));
    });

    it('carries num_ctx nested under options, never at the top level', () => {
        const body = newBody({ model, messages, settings: { ...INFERENCE_DEFAULTS, numCtx: 16384 } });
        expect(body.options).toEqual({ num_ctx: 16384 });
        expect(body).not.toHaveProperty('num_ctx');
    });

    it('carries keep_alive at the top level, never under options', () => {
        const body = newBody({ model, messages, settings: { ...INFERENCE_DEFAULTS, keepAlive: '30m' } });
        expect(body.keep_alive).toBe('30m');
        expect(body.options).toBeUndefined();
    });
});
