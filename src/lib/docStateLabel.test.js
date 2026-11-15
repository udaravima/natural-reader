import { describe, it, expect } from 'vitest';
import { docStateLabel, isMoving } from './docStateLabel';

describe('docStateLabel', () => {
  it.each([
    [{ state: 'stored', queued: true, queue_ahead: 3 }, 'Waiting to index (3 ahead)'],
    [{ state: 'stored', queued: true, queue_ahead: 0 }, 'Waiting to index'],
    [{ state: 'extracted', queued: true, queue_ahead: 2 }, 'Waiting to index (2 ahead)'],
    [{ state: 'stored', queued: false }, 'Not indexed'],
    [{ state: 'stored' }, 'Not indexed'],
    [{ state: 'extracting', queued: true, queue_ahead: 0 }, 'Reading'],
    [{ state: 'extracted' }, 'Indexing'],
    [{ state: 'indexing' }, 'Indexing'],
    [{ state: 'indexed' }, 'Indexed'],
    [{ state: 'failed' }, 'Failed'],
    [{ state: 'something-new' }, 'something-new'],
  ])('%o reads %s', (doc, label) => {
    expect(docStateLabel(doc)).toBe(label);
  });
});

describe('isMoving', () => {
  it('is true for a queued or running document, false for one nothing will index or one that has settled', () => {
    expect(isMoving({ state: 'stored', queued: true })).toBe(true);
    expect(isMoving({ state: 'indexing' })).toBe(true);
    expect(isMoving({ state: 'stored', queued: false })).toBe(false);
    expect(isMoving({ state: 'indexed' })).toBe(false);
    expect(isMoving({ state: 'failed' })).toBe(false);
  });
});
