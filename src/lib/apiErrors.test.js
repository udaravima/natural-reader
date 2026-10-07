import { describe, it, expect } from 'vitest';
import { noticeFor, describeRefusal, PROJECT_NOT_FOUND } from './apiErrors';

describe('noticeFor', () => {
    it('maps the A0 project refusals', () => {
        expect(noticeFor(403, { error: 'insufficient_role', required: 'maintainer' })).toBe('Only Maintainers or Owners can do that.');
        expect(noticeFor(403, { error: 'insufficient_role', required: 'owner' })).toBe('Only Owners can do that.');
        expect(noticeFor(403, { error: 'insufficient_role', required: 'contributor' })).toBe('Only Contributors, Maintainers or Owners can do that.');
        expect(noticeFor(403, { error: 'project_creation_restricted' })).toBe('Only admins can create projects here.');
        expect(noticeFor(409, { error: 'last_owner' })).toBe('A project must keep at least one Owner.');
        expect(noticeFor(429, { error: 'project_limit', limit: 20 })).toBe("You've reached your project limit (20). Ask an admin to raise it.");
    });
    it('lets a 404 carry its own message when asked, else the context notice', () => {
        expect(noticeFor(404, { error: 'not_found', message: 'User not found' }, { serverNotFound: true, notFound: PROJECT_NOT_FOUND })).toBe('User not found');
        expect(noticeFor(404, 'Not Found', { serverNotFound: true, notFound: PROJECT_NOT_FOUND })).toBe(PROJECT_NOT_FOUND);
        expect(noticeFor(404, { error: 'not_found', message: 'Document not found' })).toBe("This document doesn't exist or you don't have access.");
    });
    it('explains both content_shared reasons', () => {
        expect(noticeFor(409, { error: 'content_shared', reason: 'other_holders' })).toMatch(/Other people also use/);
        expect(noticeFor(409, { error: 'content_shared', reason: 'in_project' })).toMatch(/Remove it from the project first/);
    });
    it('maps size, type, empty, missing and server errors', () => {
        expect(noticeFor(413, { error: 'too_large', limit_mb: 10 })).toBe('File too large (limit 10 MB).');
        expect(noticeFor(415, { error: 'unsupported_type' })).toBe('Unsupported file type.');
        expect(noticeFor(422, { error: 'empty_file' })).toBe('The file is empty.');
        expect(noticeFor(404, 'Document not found')).toBe("This document doesn't exist or you don't have access.");
        expect(noticeFor(502, null)).toBe('Something went wrong on the server. Try again.');
    });
    it('falls back to the server message, then the status', () => {
        expect(noticeFor(409, { error: 'bytes_missing', message: 'Upload the file again first.' })).toBe('Upload the file again first.');
        expect(noticeFor(418, null)).toBe('Request failed (HTTP 418).');
    });
});

describe('describeRefusal', () => {
    it('reads detail from the body and survives a non-JSON body', async () => {
        const ok = { status: 415, json: async () => ({ detail: { error: 'unsupported_type' } }) };
        expect(await describeRefusal(ok)).toBe('Unsupported file type.');
        const bad = { status: 500, json: async () => { throw new Error('html'); } };
        expect(await describeRefusal(bad)).toBe('Something went wrong on the server. Try again.');
    });
});

describe('chat refusals', () => {
    it('uses the chat wording for a 404 when asked', () => {
        expect(noticeFor(404, { error: 'not_found' }, { notFound: 'No such chat.' })).toBe('No such chat.');
    });
    it('explains a too-large turn, a busy chat and a missing provider', () => {
        expect(noticeFor(413, { error: 'too_large', limit_mb: 25 }, { tooLarge: 'Attachments' })).toBe('Attachments too large (limit 25 MB).');
        expect(noticeFor(409, { error: 'turn_in_progress', message: 'A reply is still being written in this chat.' }))
            .toBe('A reply is still being written in this chat.');
        expect(noticeFor(503, { error: 'no_providers', message: 'No model provider is configured.' }))
            .toBe('No model provider is configured.');
    });
    it('explains a 503 with the database down instead of "server error"', () => {
        expect(noticeFor(503, { error: 'db_unavailable', message: 'Chat is offline: the database is unavailable.' }))
            .toBe('Chat is offline: the database is unavailable.');
        expect(noticeFor(503, { error: 'db_unavailable' })).toBe('The database is unavailable. Try again in a moment.');
    });
});
