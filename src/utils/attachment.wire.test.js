import { describe, it, expect } from 'vitest';
import { textAttachmentWire } from './attachment';

const staged = (over) => ({
    id: 't1', kind: 'text', name: '.env', originalText: 'KEY=hunter2\n', maskedText: 'KEY=<masked, 7 chars>\n',
    masked: 1, sendReal: false, ...over,
});

describe('textAttachmentWire real_values', () => {
    it('is true only when Send real values is on and something was masked', () => {
        expect(textAttachmentWire(staged({ sendReal: true })).wire.real_values).toBe(true);
    });
    it('is false for a masked file', () => {
        expect(textAttachmentWire(staged()).wire.real_values).toBe(false);
    });
    it('is false when nothing was masked, even with Send real values on', () => {
        expect(textAttachmentWire(staged({ sendReal: true, masked: 0, maskedText: 'KEY=1\n', originalText: 'KEY=1\n' })).wire.real_values).toBe(false);
    });
    it('sends the real text only with the choice', () => {
        expect(atob(textAttachmentWire(staged({ sendReal: true })).wire.base64)).toContain('hunter2');
        expect(atob(textAttachmentWire(staged()).wire.base64)).not.toContain('hunter2');
    });
});
