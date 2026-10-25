import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useConvertLimits } from './useConvertLimits';

const LIMITS = { enabled: true, may_convert: true, presets: ['fast'], ocr: false, picture_description: false, max_pages: 50 };
const ok = (body) => ({ ok: true, json: async () => body });

describe('useConvertLimits', () => {
    beforeEach(() => vi.clearAllMocks());

    it('asks nothing while the dialog is closed', () => {
        const { result } = renderHook(() => useConvertLimits('', '', false));
        expect(apiFetch).not.toHaveBeenCalled();
        expect(result.current).toBeNull();
    });

    it('fetches when the dialog opens, and again on every later opening', async () => {
        apiFetch.mockResolvedValue(ok(LIMITS));
        const { result, rerender } = renderHook(({ open }) => useConvertLimits('', '', open), { initialProps: { open: false } });
        rerender({ open: true });
        await waitFor(() => expect(result.current).toEqual(LIMITS));
        expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/settings/docling');

        rerender({ open: false });
        rerender({ open: true });
        expect(result.current).toBeNull();   // no stale answer from the last opening
        await waitFor(() => expect(result.current).toEqual(LIMITS));
        expect(apiFetch).toHaveBeenCalledTimes(2);
    });

    it('stays null when the server refuses or the call fails', async () => {
        apiFetch.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });
        const { result, rerender } = renderHook(({ open }) => useConvertLimits('', '', open), { initialProps: { open: true } });
        await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
        expect(result.current).toBeNull();

        apiFetch.mockRejectedValueOnce(new Error('offline'));
        rerender({ open: false });
        rerender({ open: true });
        await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
        expect(result.current).toBeNull();
    });
});
