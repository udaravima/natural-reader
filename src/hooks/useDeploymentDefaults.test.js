import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useDeploymentDefaults, applyEpochs, BUILT_IN_DEFAULTS } from './useDeploymentDefaults';
import { usePersistedState } from './usePersistedState';

const ok = (body) => ({ ok: true, json: async () => body });

describe('useDeploymentDefaults', () => {
    beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });

    it('starts from the built-in defaults and takes the deployment\'s once signed in', async () => {
        apiFetch.mockResolvedValue(ok({ values: { ...BUILT_IN_DEFAULTS, chatAutoTts: true }, epochs: {} }));
        const { result } = renderHook(() => useDeploymentDefaults({ active: true, apiHost: '', apiPort: '' }));
        expect(result.current.chatAutoTts).toBe(false);
        await waitFor(() => expect(result.current.chatAutoTts).toBe(true));
    });

    it('asks nothing before sign-in', () => {
        renderHook(() => useDeploymentDefaults({ active: false, apiHost: '', apiPort: '' }));
        expect(apiFetch).not.toHaveBeenCalled();
    });

    it('an unchosen setting follows the deployment default', async () => {
        apiFetch.mockResolvedValue(ok({ values: { ...BUILT_IN_DEFAULTS, playbackSpeed: 1.5 }, epochs: {} }));
        const { result } = renderHook(() => {
            const d = useDeploymentDefaults({ active: true, apiHost: '', apiPort: '' });
            return usePersistedState('playbackSpeed', d.playbackSpeed)[0];
        });
        await waitFor(() => expect(result.current).toBe(1.5));
    });
});

describe('applyEpochs (Apply to everyone)', () => {
    beforeEach(() => localStorage.clear());

    it('drops the user\'s own choice once per new epoch', () => {
        const { result } = renderHook(() => usePersistedState('chatAutoTts', false));
        act(() => result.current[1](true));
        act(() => applyEpochs({ chatAutoTts: 1 }));
        expect(result.current[0]).toBe(false);
        act(() => result.current[1](true));       // the user chooses again
        act(() => applyEpochs({ chatAutoTts: 1 }));   // same epoch: their new choice stays
        expect(result.current[0]).toBe(true);
    });

    it('hands think to onResetThink', () => {
        const onResetThink = vi.fn();
        applyEpochs({ think: 3 }, { onResetThink });
        applyEpochs({ think: 3 }, { onResetThink });
        expect(onResetThink).toHaveBeenCalledTimes(1);
    });
});
