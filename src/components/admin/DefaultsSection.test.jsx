import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { DefaultsSection } from './DefaultsSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { border: '', bg: '', bgSecondary: '', text: '', textSecondary: '', textMuted: '' };
const BUILT_IN = { chatAutoTts: false, chatTtsMode: 'after-complete', think: 'off', selectedVoice: 'af_heart', playbackSpeed: 1 };
const view = (over = {}) => ({
    values: { ...BUILT_IN }, builtIn: BUILT_IN, overridden: [], epochs: {},
    choices: { chatTtsMode: ['streaming', 'after-complete'], think: ['off', 'on', 'low', 'medium', 'high'] }, ...over,
});
const json = (body) => new Response(JSON.stringify(body), { status: 200 });
const props = { theme, apiHost: '', apiPort: '', showToast: vi.fn() };

describe('Admin → Defaults', () => {
    beforeEach(() => vi.clearAllMocks());

    it('shows each default and saves a change', async () => {
        apiFetch.mockResolvedValueOnce(json(view()));
        apiFetch.mockResolvedValueOnce(json(view({ values: { ...BUILT_IN, chatAutoTts: true }, overridden: ['chatAutoTts'] })));
        render(<DefaultsSection {...props} />);
        const auto = await screen.findByRole('checkbox', { name: /read replies aloud/i });
        expect(auto).not.toBeChecked();
        fireEvent.click(auto);
        fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
        await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/admin/defaults', expect.objectContaining({ method: 'PUT' })));
        const body = JSON.parse(apiFetch.mock.calls[1][3].body);
        expect(body.values.chatAutoTts).toBe(true);
    });

    it('Apply to everyone asks first, then calls the action', async () => {
        apiFetch.mockResolvedValueOnce(json(view()));
        apiFetch.mockResolvedValueOnce(json(view({ epochs: { think: 1 } })));
        render(<DefaultsSection {...props} />);
        await screen.findByRole('checkbox', { name: /read replies aloud/i });
        fireEvent.click(screen.getByRole('button', { name: /apply thinking to everyone/i }));
        expect(apiFetch).toHaveBeenCalledTimes(1);   // only asked so far
        expect(screen.getByText(/replaces everyone's own choice/i)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /^replace for everyone$/i }));
        await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/admin/defaults/think/apply-to-everyone', expect.objectContaining({ method: 'POST' })));
    });

    it('marks a default that differs from the built-in', async () => {
        apiFetch.mockResolvedValueOnce(json(view({ values: { ...BUILT_IN, think: 'on' }, overridden: ['think'] })));
        render(<DefaultsSection {...props} />);
        expect(await screen.findByText(/built-in: off/i)).toBeInTheDocument();
    });
});
