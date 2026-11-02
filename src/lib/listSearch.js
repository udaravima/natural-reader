// How long a list gets before it earns a search box (components/SearchableList.jsx), and how a search matches.
export const LONG_LIST = 12;

// "new york" finds America/New_York, and "sao" finds São Paulo.
const fold = (text) => String(text ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[_/-]+/g, ' ');

export const hits = (query, ...texts) => {
    const words = fold(query).split(/\s+/).filter(Boolean);
    const haystack = texts.map(fold).join(' ');
    return words.every((word) => haystack.includes(word));
};
