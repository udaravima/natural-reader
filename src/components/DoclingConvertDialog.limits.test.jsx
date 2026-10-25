import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import DoclingConvertDialog from './DoclingConvertDialog';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const OFF = 'Turned off by your admin';

const OPEN_LIMITS = { enabled: true, may_convert: true, presets: ['fast', 'standard', 'accurate'], ocr: true, picture_description: true, max_pages: 0 };

function renderDialog(props = {}) {
    const onSubmit = vi.fn();
    const utils = render(
        <DoclingConvertDialog
            theme={theme} darkMode={false} open onClose={() => {}} onSubmit={onSubmit}
            pageCount={10} initialOptions={null} {...props}
        />,
    );
    return { onSubmit, ...utils };
}

const convertButton = () => screen.getByRole('button', { name: 'Convert' });

describe('DoclingConvertDialog limits', () => {
    it('greys out what the admin turned off', () => {
        renderDialog({ limits: { ...OPEN_LIMITS, presets: ['fast', 'standard'], ocr: false, picture_description: false } });
        expect(screen.getByRole('button', { name: /Accurate/ })).toBeDisabled();
        expect(screen.getByRole('checkbox', { name: /Force OCR/ })).toBeDisabled();
        expect(screen.getByRole('button', { name: /Describe/ })).toBeDisabled();
        expect(screen.getAllByText(OFF)).toHaveLength(3);
        // what is still allowed stays usable
        expect(screen.getByRole('button', { name: /Fast/ })).toBeEnabled();
        expect(screen.getByRole('button', { name: /Embed/ })).toBeEnabled();
    });

    it('opens on an allowed preset when the saved one is turned off', () => {
        const { onSubmit } = renderDialog({
            initialOptions: { preset: 'accurate', ocr: true, images: 'describe' },
            limits: { ...OPEN_LIMITS, presets: ['fast', 'standard'], ocr: false, picture_description: false },
        });
        fireEvent.click(convertButton());
        expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ preset: 'fast', ocr: false, images: 'drop' }));
    });

    it('moves off a turned-off choice when the limits arrive after the dialog opened', () => {
        const initialOptions = { preset: 'accurate', ocr: true, images: 'describe' };
        const onSubmit = vi.fn();
        const props = { theme, darkMode: false, open: true, onClose: () => {}, onSubmit, pageCount: 10, initialOptions };
        const { rerender } = render(<DoclingConvertDialog {...props} limits={null} />);
        rerender(<DoclingConvertDialog {...props} limits={{ ...OPEN_LIMITS, presets: ['standard'], ocr: false, picture_description: false }} />);
        fireEvent.click(convertButton());
        expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ preset: 'standard', ocr: false, images: 'drop' }));
    });

    it('asks for a page range over the page limit', () => {
        const { onSubmit } = renderDialog({ pageCount: 300, limits: { ...OPEN_LIMITS, max_pages: 100 } });
        expect(screen.getByText('This server converts at most 100 pages at a time. Choose a page range of 100 pages or fewer.')).toBeInTheDocument();
        expect(convertButton()).toBeDisabled();

        fireEvent.click(screen.getByRole('checkbox', { name: /Limit page range/ }));
        const [start, end] = screen.getAllByRole('spinbutton');
        fireEvent.change(start, { target: { value: '1' } });
        fireEvent.change(end, { target: { value: '100' } });
        expect(screen.queryByText(/This server converts at most/)).not.toBeInTheDocument();
        expect(convertButton()).toBeEnabled();
        fireEvent.click(convertButton());
        expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ page_range: [1, 100] }));
    });

    it('keeps Convert off while the range is still over the limit', () => {
        renderDialog({ pageCount: 300, limits: { ...OPEN_LIMITS, max_pages: 100 } });
        fireEvent.click(screen.getByRole('checkbox', { name: /Limit page range/ }));
        const [start, end] = screen.getAllByRole('spinbutton');
        fireEvent.change(start, { target: { value: '1' } });
        fireEvent.change(end, { target: { value: '150' } });
        expect(convertButton()).toBeDisabled();
    });

    it('tells a reader without the Docling role they may not convert', () => {
        renderDialog({ limits: { ...OPEN_LIMITS, may_convert: false } });
        expect(screen.getByText('Only people your admin has given the Docling role can convert documents on this server.')).toBeInTheDocument();
        expect(convertButton()).toBeDisabled();
    });

    it('says so when no preset is allowed', () => {
        renderDialog({ limits: { ...OPEN_LIMITS, presets: [] } });
        expect(screen.getByText("Your admin hasn't allowed any conversion preset.")).toBeInTheDocument();
        expect(convertButton()).toBeDisabled();
    });

    it('behaves as before without limits', () => {
        const { onSubmit } = renderDialog({ limits: null });
        expect(screen.queryByText(OFF)).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Accurate/ })).toBeEnabled();
        expect(screen.getByRole('checkbox', { name: /Force OCR/ })).toBeEnabled();
        expect(screen.getByRole('button', { name: /Describe/ })).toBeEnabled();
        expect(convertButton()).toBeEnabled();
        fireEvent.click(convertButton());
        expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ preset: 'standard' }));
    });
});
