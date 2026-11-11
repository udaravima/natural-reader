/** "just now", "5 min ago", "3 h ago", "2 d ago", then the date: for activity lines and notifications. */
export function timeAgo(at, now = Date.now()) {
    const s = Math.max(0, Math.round((now - new Date(at).getTime()) / 1000));
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
    return new Date(at).toLocaleDateString();
}
