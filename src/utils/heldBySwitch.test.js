import { describe, it, expect } from 'vitest';
import { heldBySwitch } from './heldBySwitch';

const A = 'ollama:gemma4:e4b';
const B = 'ollama:gemma4:31b-cloud';
const real = (name = '.env') => ({ kind: 'text', name, real_values: true });
const user = (model, attachments = []) => ({ role: 'user', model, attachments });
const reply = (model) => ({ role: 'assistant', model, attachments: [] });

describe('heldBySwitch', () => {
    it('holds nothing while the model the values were agreed for is selected', () => {
        expect(heldBySwitch([user(A, [real()]), reply(A)], A)).toEqual({ names: [], replies: 0, agreedFor: [] });
    });

    it('names the file, counts the replies that read it, and says which model to go back to', () => {
        const messages = [user(A), reply(A), user(A, [real()]), reply(A), user(A), reply(A)];
        expect(heldBySwitch(messages, B)).toEqual({ names: ['.env'], replies: 2, agreedFor: [A] });
    });

    it('keeps replies from before the real values were sent, and replies of a model never given them', () => {
        const messages = [user(A), reply(A), user(B, [real()]), reply(B), user(A), reply(A)];
        // For A: B's file and B's reply are held; A's own replies stay.
        expect(heldBySwitch(messages, A)).toEqual({ names: ['.env'], replies: 1, agreedFor: [B] });
    });

    it('holds nothing for a masked file, a picture, or a file with nothing to mask', () => {
        const messages = [
            user(A, [{ kind: 'text', name: '.env', masked: true }, { kind: 'image', name: 'a.png' },
                { kind: 'text', name: 'notes.txt', real_values: false }]),
            reply(A),
        ];
        expect(heldBySwitch(messages, B)).toEqual({ names: [], replies: 0, agreedFor: [] });
    });

    it('holds a reply with no model recorded once real values are in the conversation', () => {
        expect(heldBySwitch([user(A, [real()]), { role: 'assistant' }], B).replies).toBe(1);
    });

    it('lists a file once, and holds nothing when no model is selected', () => {
        const messages = [user(A, [real()]), reply(A), user(A, [real()]), reply(A)];
        expect(heldBySwitch(messages, B).names).toEqual(['.env']);
        expect(heldBySwitch(messages, '')).toEqual({ names: [], replies: 0, agreedFor: [] });
    });
});
