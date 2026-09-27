import { describe, it, expect } from 'vitest';
import { capabilityBadges, groupByProvider, migrateModelId, modelLabel, supportedKnobs } from './modelIds';

const m = (provider, kind, name, capabilities = {}) => ({ id: `${provider}:${name}`, provider, kind, name, capabilities });
const MODELS = [m('local', 'ollama', 'qwen2.5:7b', { tools: true, thinking: false }),
                m('local', 'ollama', 'gemma3', { vision: true }),
                m('cloud', 'openai', 'vendor/x', { tools: true, thinking: true })];

describe('modelIds', () => {
    it('keeps a current id and maps a pre-C1 bare name to its ollama id', () => {
        expect(migrateModelId('cloud:vendor/x', MODELS)).toBe('cloud:vendor/x');
        expect(migrateModelId('qwen2.5:7b', MODELS)).toBe('local:qwen2.5:7b');
        expect(migrateModelId('gone', MODELS)).toBe('gone');
        expect(migrateModelId('qwen2.5:7b', [])).toBe('qwen2.5:7b');
    });
    it('groups by provider in server order', () => {
        expect(groupByProvider(MODELS).map(([p, ms]) => [p, ms.length])).toEqual([['local', 2], ['cloud', 1]]);
    });
    it('labels with capability badges', () => {
        expect(capabilityBadges(MODELS[2])).toEqual(['tools', 'thinking']);
        expect(modelLabel(MODELS[1])).toBe('gemma3 · vision');
        expect(modelLabel(m('local', 'ollama', 'plain'))).toBe('plain');
    });
    it('offers only the knobs the provider supports', () => {
        expect(supportedKnobs(MODELS[0])).toEqual({ numCtx: true, keepAlive: true, think: false, numPredict: true });
        expect(supportedKnobs(MODELS[2])).toEqual({ numCtx: false, keepAlive: false, think: true, numPredict: true });
        expect(supportedKnobs(undefined)).toEqual({ numCtx: true, keepAlive: true, think: true, numPredict: true });
    });
});
