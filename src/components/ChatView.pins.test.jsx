import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DocContextChip } from './ChatView';

const theme = { border: '', text: '', textMuted: '', textSecondary: '', hover: '' };

describe('DocContextChip', () => {
    it('renders the pin label + file and fires onRemove with no retrieval toggle', () => {
        const onRemove = vi.fn();
        render(<DocContextChip ctx={{ kind: 'selection', page: 2, fileName: 'moon.md', text: 'cheese' }} theme={theme} darkMode={false} onRemove={onRemove} />);
        expect(screen.getByText('Selection')).toBeInTheDocument();
        expect(screen.getByText(/moon\.md/)).toBeInTheDocument();
        expect(screen.queryByText('Use whole document')).not.toBeInTheDocument();
        fireEvent.click(screen.getByTitle('Remove context'));
        expect(onRemove).toHaveBeenCalledTimes(1);
    });
});
