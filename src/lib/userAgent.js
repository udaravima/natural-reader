// "Chrome on Linux" from a browser's User-Agent header, for the list of
// signed-in browsers (Settings → Profile). Order matters: Edge and Opera
// also say "Chrome", and Chrome also says "Safari".
const BROWSERS = [
    [/Edg(e|A|iOS)?\//, 'Edge'],
    [/OPR\/|Opera/, 'Opera'],
    [/Firefox\/|FxiOS\//, 'Firefox'],
    [/Chrome\/|CriOS\//, 'Chrome'],
    [/Safari\//, 'Safari'],
];
const SYSTEMS = [
    [/Android/, 'Android'],
    [/iPhone|iPad|iPod/, 'iOS'],
    [/Windows/, 'Windows'],
    [/Mac OS X|Macintosh/, 'macOS'],
    [/CrOS/, 'ChromeOS'],
    [/Linux/, 'Linux'],
];

export function describeUserAgent(ua) {
    if (!ua) return 'Unknown browser';
    const browser = BROWSERS.find(([re]) => re.test(ua))?.[1];
    const system = SYSTEMS.find(([re]) => re.test(ua))?.[1];
    if (browser && system) return `${browser} on ${system}`;
    return browser || system || 'Unknown browser';
}
