import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { pollIndexUntilSettled, pollConvertUntilSettled } from './docStatusPoller';

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

describe('pollIndexUntilSettled', () => {
    beforeEach(() => vi.clearAllMocks());

    it('calls the server id but writes progress under stateKey when the two differ', async () => {
        // Regression for: a client/server hash mismatch left IndexButton
        // frozen because progress was written under the server's id while
        // the button reads state keyed by the client's local hash.
        apiFetch.mockResolvedValue(json(200, { state: 'indexed', chunk_count: 9, embedded_count: 9 }));
        const setDocIndexByDocId = vi.fn();
        const showToast = vi.fn();

        await pollIndexUntilSettled({
            apiDocId: 'server-hash', stateKey: 'local-hash',
            apiHost: '', apiPort: '', setDocIndexByDocId, showToast, pollMs: 0,
        });

        // The network call is authoritative: it targets the server's id.
        expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/docs/server-hash');

        // The UI state IndexButton actually reads lands under the *local*
        // id the flow started with, not the server's id.
        const next = setDocIndexByDocId.mock.calls[0][0]({});
        expect(next).toEqual({ 'local-hash': { state: 'indexed', chunkCount: 9, embeddedCount: 9 } });
        expect(next['server-hash']).toBeUndefined();
        expect(showToast).toHaveBeenCalledWith('Indexed 9 chunks.', 3000);
    });

    it('stops and toasts on a failed state', async () => {
        apiFetch.mockResolvedValue(json(200, { state: 'failed', error_message: 'boom' }));
        const setDocIndexByDocId = vi.fn();
        const showToast = vi.fn();

        await pollIndexUntilSettled({
            apiDocId: 'a', stateKey: 'a', apiHost: '', apiPort: '', setDocIndexByDocId, showToast, pollMs: 0,
        });

        expect(showToast).toHaveBeenCalledWith('Indexing failed: boom', 6000);
    });
});

describe('pollConvertUntilSettled', () => {
    beforeEach(() => vi.clearAllMocks());

    it('calls the server id but writes convert/index/view progress under stateKey when the two differ', async () => {
        // Same regression, for the convert flow's identical docId →
        // convertDocId pattern (docConvertByDocId[currentDocId]).
        apiFetch.mockResolvedValue(json(200, {
            conversion_state: 'converted', converted_page_count: 12,
            state: 'indexed', chunk_count: 5, embedded_count: 5,
        }));
        const setDocConvertByDocId = vi.fn();
        const setDocIndexByDocId = vi.fn();
        const setDocViewByDocId = vi.fn();
        const showToast = vi.fn();

        await pollConvertUntilSettled({
            apiDocId: 'server-hash', stateKey: 'local-hash',
            apiHost: '', apiPort: '',
            setDocConvertByDocId, setDocIndexByDocId, setDocViewByDocId, showToast, pollMs: 0,
        });

        expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/docs/server-hash');

        expect(setDocConvertByDocId.mock.calls[0][0]({})).toEqual({
            'local-hash': { state: 'converted', pageCount: 12, error: null, options: null, hasPdf: false },
        });
        expect(setDocIndexByDocId.mock.calls[0][0]({})).toEqual({
            'local-hash': { state: 'indexed', chunkCount: 5, embeddedCount: 5 },
        });
        // The reader auto-switches to the converted MD view — also keyed by
        // the local id, so it isn't stranded on a server-id key nothing reads.
        expect(setDocViewByDocId.mock.calls[0][0]({})).toEqual({ 'local-hash': 'md' });
    });

    it('stops and toasts on a conversion failure without touching the view', async () => {
        apiFetch.mockResolvedValue(json(200, { conversion_state: 'conversion_failed', conversion_error: 'bad pdf' }));
        const setDocConvertByDocId = vi.fn();
        const setDocIndexByDocId = vi.fn();
        const setDocViewByDocId = vi.fn();
        const showToast = vi.fn();

        await pollConvertUntilSettled({
            apiDocId: 'a', stateKey: 'a', apiHost: '', apiPort: '',
            setDocConvertByDocId, setDocIndexByDocId, setDocViewByDocId, showToast, pollMs: 0,
        });

        expect(showToast).toHaveBeenCalledWith('Conversion failed: bad pdf', 6000);
        expect(setDocViewByDocId).not.toHaveBeenCalled();
    });
});
