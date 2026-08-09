import { describe, it, expect } from 'vitest';
import {
    INFERENCE_DEFAULTS, resolveForModel, patchForModel, buildRequestFields,
    migrateLegacyThinking, estimateTokens, truncationMessage,
} from './inference';

describe('resolveForModel', () => {
    it('returns defaults for an unknown model', () => {
        expect(resolveForModel({}, 'nope:latest')).toEqual(INFERENCE_DEFAULTS);
    });
    it('returns defaults when the map is null or undefined', () => {
        expect(resolveForModel(null, 'a')).toEqual(INFERENCE_DEFAULTS);
        expect(resolveForModel(undefined, 'a')).toEqual(INFERENCE_DEFAULTS);
    });
    it('merges a partial stored entry over the defaults', () => {
        const map = { 'qwen:latest': { numCtx: 16384 } };
        expect(resolveForModel(map, 'qwen:latest')).toEqual({
            numCtx: 16384, keepAlive: null, think: 'off', numPredict: null,
        });
    });
});

describe('patchForModel', () => {
    it('stores a patch under the model key', () => {
        const next = patchForModel({}, 'qwen:latest', { numCtx: 8192 });
        expect(next['qwen:latest'].numCtx).toBe(8192);
    });
    it('does not mutate the input map', () => {
        const map = { 'a:latest': { numCtx: 4096 } };
        const next = patchForModel(map, 'a:latest', { numCtx: 8192 });
        expect(map['a:latest'].numCtx).toBe(4096);
        expect(next).not.toBe(map);
    });
    it('leaves other models untouched', () => {
        const map = { 'a:latest': { numCtx: 4096 } };
        const next = patchForModel(map, 'b:latest', { numCtx: 8192 });
        expect(next['a:latest']).toEqual({ numCtx: 4096 });
    });
});

describe('buildRequestFields', () => {
    it('emits only think:false when everything is unset', () => {
        expect(buildRequestFields(INFERENCE_DEFAULTS)).toEqual({ think: false });
    });
    it('treats a missing settings object as all-unset', () => {
        expect(buildRequestFields(undefined)).toEqual({ think: false });
    });
    it('maps think "on" to the boolean true', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, think: 'on' })).toEqual({ think: true });
    });
    it('passes think levels through as strings', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, think: 'low' })).toEqual({ think: 'low' });
    });
    it('adds options.num_ctx alone without num_predict', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, numCtx: 16384 })).toEqual({
            think: false, options: { num_ctx: 16384 },
        });
    });
    it('adds options.num_predict alone without num_ctx', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, numPredict: 512 })).toEqual({
            think: false, options: { num_predict: 512 },
        });
    });
    it('adds keep_alive as a string', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, keepAlive: '30m' })).toEqual({
            think: false, keep_alive: '30m',
        });
    });
    it('adds keep_alive as -1 for Always', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, keepAlive: -1 })).toEqual({
            think: false, keep_alive: -1,
        });
    });
    it('never emits an options key when both options are unset', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, keepAlive: '5m' })).not.toHaveProperty('options');
    });
});

describe('migrateLegacyThinking', () => {
    it('maps true to on and false to off', () => {
        expect(migrateLegacyThinking(true)).toBe('on');
        expect(migrateLegacyThinking(false)).toBe('off');
    });
});

describe('estimateTokens', () => {
    it('is zero for an empty array', () => {
        expect(estimateTokens([])).toBe(0);
    });
    it('approximates four characters per token', () => {
        expect(estimateTokens(['a'.repeat(400)])).toBe(100);
    });
    it('sums across entries and ignores non-strings', () => {
        expect(estimateTokens(['a'.repeat(8), null, undefined, 'b'.repeat(4)])).toBe(3);
    });
});

describe('truncationMessage', () => {
    it('is null when the model stopped normally', () => {
        expect(truncationMessage({ doneReason: 'stop', promptEvalCount: 10, evalCount: 5 })).toBeNull();
    });
    it('is null when there are no stats at all', () => {
        expect(truncationMessage(null)).toBeNull();
        expect(truncationMessage(undefined)).toBeNull();
    });
    it('names the prompt, reply, and total when the context filled up', () => {
        const msg = truncationMessage({ doneReason: 'length', promptEvalCount: 3317, evalCount: 779 });
        expect(msg).toContain('3317');
        expect(msg).toContain('779');
        expect(msg).toContain('4096');
    });
    it('treats missing counts as zero rather than printing undefined', () => {
        const msg = truncationMessage({ doneReason: 'length' });
        expect(msg).toContain('0 prompt + 0 reply = 0');
        expect(msg).not.toContain('undefined');
    });

    // Ollama reports done_reason "length" for two different events: the
    // context window filled up, OR num_predict (Max reply tokens) was
    // reached. These need different advice, so the branch has to actually
    // distinguish them rather than always blaming the context window.
    it('names the reply cap when num_predict was reached', () => {
        const msg = truncationMessage(
            { doneReason: 'length', promptEvalCount: 100, evalCount: 512 },
            { numPredict: 512 },
        );
        expect(msg).toContain('512-token reply cap');
        expect(msg).toContain('Max reply tokens');
        expect(msg).not.toContain('context window');
    });

    it('still blames the context window when num_predict is unset', () => {
        const msg = truncationMessage(
            { doneReason: 'length', promptEvalCount: 3317, evalCount: 779 },
            { numPredict: null },
        );
        expect(msg).toContain('Raise the context window');
        expect(msg).not.toContain('reply cap');
    });

    it('still blames the context window when num_predict is set but the reply came in under it', () => {
        const msg = truncationMessage(
            { doneReason: 'length', promptEvalCount: 4000, evalCount: 90 },
            { numPredict: 512 },
        );
        expect(msg).toContain('Raise the context window');
        expect(msg).not.toContain('reply cap');
    });
});
