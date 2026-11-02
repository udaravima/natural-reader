import { describe, it, expect } from 'vitest';
import { formatDay, zoneOptions } from './zoneFormat';

describe('formatDay', () => {
  it('formats a day in the deployment zone', () => {
    expect(formatDay('2026-10-25T20:00:00Z', 'Asia/Colombo')).toBe('2026-10-26');
    expect(formatDay('2026-10-25T20:00:00Z', 'UTC')).toBe('2026-10-25');
    expect(formatDay('2026-10-25T20:00:00Z', 'Nope/Nope')).toBe('2026-10-25');
  });

  it('shows the first 10 characters of a value that is not a date', () => {
    expect(formatDay('not a date at all', 'UTC')).toBe('not a date');
  });
});

describe('zoneOptions', () => {
  it('lists UTC first, once, then the browser zones', () => {
    const zones = zoneOptions();
    expect(zones[0]).toBe('UTC');
    expect(zones.filter((z) => z === 'UTC')).toHaveLength(1);
    expect(zones).toContain('Asia/Colombo');
  });

  it('adds the current zone when the list lacks it', () => {
    expect(zoneOptions('Mars/Olympus').at(-1)).toBe('Mars/Olympus');
    expect(zoneOptions('Asia/Colombo').filter((z) => z === 'Asia/Colombo')).toHaveLength(1);
  });

  it('is just UTC (and the current zone) where the browser cannot list zones', () => {
    const original = Intl.supportedValuesOf;
    Intl.supportedValuesOf = undefined;
    try {
      expect(zoneOptions()).toEqual(['UTC']);
      expect(zoneOptions('Asia/Colombo')).toEqual(['UTC', 'Asia/Colombo']);
    } finally {
      Intl.supportedValuesOf = original;
    }
  });
});
