/**
 * Build an API URL from configurable host + port settings.
 *
 * Three modes:
 *   - Empty `host`        → returns the path as-is (relative URL). The browser
 *                           hits the same origin the page was loaded from. Use
 *                           this when the app sits behind a reverse proxy that
 *                           routes /v1/* to Kokoro and /api/* to Ollama.
 *   - Host with `http(s)://` prefix → the prefix is preserved; protocol-aware.
 *   - Bare host (`localhost`, `192.168.1.10`, …) → defaults to `http://`.
 *
 * Port is appended only when present (and only when the host doesn't already
 * include a port — e.g. someone typing `https://example.com:8443` is honored).
 */
export const buildApiUrl = (host, port, endpoint) => {
    const h = (host || '').trim();
    if (!h) return endpoint;
    const hasProto = /^https?:\/\//i.test(h);
    const hasPortInHost = /:\d+(\/|$)/.test(h.replace(/^https?:\/\//i, ''));
    const portStr = port == null ? '' : String(port).trim();
    const portPart = portStr && !hasPortInHost ? `:${portStr}` : '';
    const prefix = hasProto ? h : `http://${h}`;
    // Trim any trailing slash on the host part so we don't end up with `//path`.
    const normalized = prefix.replace(/\/+$/, '');
    return `${normalized}${portPart}${endpoint}`;
};
