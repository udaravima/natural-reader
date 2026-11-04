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
const NAMES = { choice_labels: { speechLanguage: { de: 'German', en: 'English' } } };
// Answers by route: the first GET, then whatever a save or an action answers.
function api({ first, then }) {
    apiFetch.mockImplementation(async (h, p, path, opts) => (opts?.method ? json(then) : json(first)));
}
const nonStatus = () => apiFetch.mock.calls;
const props = { theme, apiHost: '', apiPort: '', showToast: vi.fn() };

describe('Admin → Defaults', () => {
    beforeEach(() => vi.clearAllMocks());

    it('shows each default and saves a change', async () => {
        api({ first: view(), then: view({ values: { ...BUILT_IN, chatAutoTts: true }, overridden: ['chatAutoTts'] }) });
        render(<DefaultsSection {...props} />);
        const auto = await screen.findByRole('checkbox', { name: /read replies aloud/i });
        expect(auto).not.toBeChecked();
        fireEvent.click(auto);
        fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
        await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/admin/defaults', expect.objectContaining({ method: 'PUT' })));
        const body = JSON.parse(nonStatus()[1][3].body);
        expect(body.values.chatAutoTts).toBe(true);
    });

    it('Apply to everyone asks first, then calls the action', async () => {
        api({ first: view(), then: view({ epochs: { think: 1 } }) });
        render(<DefaultsSection {...props} />);
        await screen.findByRole('checkbox', { name: /read replies aloud/i });
        fireEvent.click(screen.getByRole('button', { name: /apply thinking to everyone/i }));
        expect(nonStatus()).toHaveLength(1);   // only asked so far
        expect(screen.getByText(/replaces everyone's own choice/i)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /^replace for everyone$/i }));
        await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/admin/defaults/think/apply-to-everyone', expect.objectContaining({ method: 'POST' })));
    });

    // The same steps as a person's speed slider (Settings → Voice & reading), so any default is one they can pick.
    it('the reading speed moves in steps of 0.05, from 0.5× to 2×', async () => {
        api({ first: view() });
        render(<DefaultsSection {...props} />);
        const speed = await screen.findByRole('spinbutton', { name: 'Reading speed' });
        expect(speed).toHaveAttribute('step', '0.05');
        expect(speed).toHaveAttribute('min', '0.5');
        expect(speed).toHaveAttribute('max', '2');
    });

    it('marks a default that differs from the built-in', async () => {
        api({ first: view({ values: { ...BUILT_IN, think: 'on' }, overridden: ['think'] }) });
        render(<DefaultsSection {...props} />);
        expect(await screen.findByText(/built-in: off/i)).toBeInTheDocument();
    });

    describe('Speech language', () => {
        const withSpeech = (values = {}, over = {}) => view({
            values: { ...BUILT_IN, speechLanguage: 'auto', ...values },
            builtIn: { ...BUILT_IN, speechLanguage: 'auto' }, ...NAMES, ...over,
        });

        it('never calls the speech status route (a person without chat access would be bounced)', async () => {
            api({ first: withSpeech() });
            render(<DefaultsSection {...props} />);
            await screen.findByRole('combobox', { name: 'Speech language' });
            expect(apiFetch.mock.calls.map((c) => c[2])).toEqual(['/v1/admin/defaults']);
        });

        it('is a labelled select with Auto first, then the engine\'s languages by name', async () => {
            api({ first: withSpeech() });
            render(<DefaultsSection {...props} />);
            const select = await screen.findByRole('combobox', { name: 'Speech language' });
            expect([...select.options].map((o) => o.textContent)).toEqual(['Auto', 'English', 'German']);
            expect(select.value).toBe('auto');
        });

        it('saves a chosen language as its code', async () => {
            api({ first: withSpeech(), then: withSpeech({ speechLanguage: 'de' }, { overridden: ['speechLanguage'] }) });
            render(<DefaultsSection {...props} />);
            const select = await screen.findByRole('combobox', { name: 'Speech language' });
            fireEvent.change(select, { target: { value: 'de' } });
            fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
            await waitFor(() => expect(nonStatus()).toHaveLength(2));
            expect(JSON.parse(nonStatus()[1][3].body).values.speechLanguage).toBe('de');
        });

        it('shows a language name, not a code, in the built-in note and the confirmation', async () => {
            api({ first: withSpeech({ speechLanguage: 'de' }, { builtIn: { ...BUILT_IN, speechLanguage: 'auto' }, overridden: ['speechLanguage'] }) });
            render(<DefaultsSection {...props} />);
            await screen.findByRole('combobox', { name: 'Speech language' });
            fireEvent.click(screen.getByRole('button', { name: /apply speech language to everyone/i }));
            expect(await screen.findByText(/replaces everyone's own choice with German/i)).toBeInTheDocument();
            expect(screen.getByText(/built-in: Auto/i)).toBeInTheDocument();
        });

        it('falls back to the code when the view carries no names', async () => {
            api({ first: withSpeech({ speechLanguage: 'de' }, { choice_labels: undefined }) });
            render(<DefaultsSection {...props} />);
            const select = await screen.findByRole('combobox', { name: 'Speech language' });
            expect([...select.options].map((o) => o.textContent)).toEqual(['Auto', 'de']);
            expect(select.value).toBe('de');
        });
    });
});
