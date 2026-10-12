import { describe, it, expect } from 'vitest';
import { oldVersionOf } from './versions';

const item = (name, isCurrent) => ({ project: { id: name, name }, document_id: `${name}-d`, version: 1,
  current_version: 3, current_doc_id: 'h3', current_name: 'r.pdf', is_current: isCurrent });

describe('oldVersionOf (V1 §7 banner rule)', () => {
  it('is the first item when the content is old everywhere it appears', () => {
    expect(oldVersionOf([item('Alpha', false), item('Beta', false)]).project.name).toBe('Alpha');
  });
  it('is null when it is current somewhere (the README case)', () => {
    expect(oldVersionOf([item('X', false), item('Y', true)])).toBeNull();
  });
  it('is null for nothing, or a non-list', () => {
    expect(oldVersionOf([])).toBeNull();
    expect(oldVersionOf(undefined)).toBeNull();
  });
});
