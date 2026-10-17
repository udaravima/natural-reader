import { describe, it, expect } from 'vitest';
import { describeUserAgent } from './userAgent';

describe('describeUserAgent', () => {
    it.each([
        ['Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36', 'Chrome on Linux'],
        ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 Edg/140.0', 'Edge on Windows'],
        ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15', 'Safari on macOS'],
        ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/140.0 Mobile/15E148 Safari/604.1', 'Chrome on iOS'],
        ['Mozilla/5.0 (Android 15; Mobile; rv:131.0) Gecko/131.0 Firefox/131.0', 'Firefox on Android'],
        ['curl/8.5.0', 'Unknown browser'],
        [null, 'Unknown browser'],
    ])('%s → %s', (ua, expected) => {
        expect(describeUserAgent(ua)).toBe(expected);
    });
});
