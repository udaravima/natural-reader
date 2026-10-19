import { useState } from 'react';
import { Globe } from 'lucide-react';

const MAX_QUERY = 300;    // characters: the server refuses a longer one (WebIn in chat_turns.py)
const MAX_REASON = 300;   // characters shown; the rest is cut and marked with an ellipsis

// The reason and the query are the model's words: shown as text only, and the
// server skips its own check of an approved query because the user saw it, so
// what is shown must be exactly what is searched. Tabs, line breaks, U+0085,
// U+2028 and U+2029 become one space; then everything the user can't see is
// dropped: the control characters (U+0000-U+001F, U+007F-U+009F), every Unicode
// format character (\p{Cf}: direction marks and overrides, zero-width characters,
// the soft hyphen, most of the Tag block, ...) and the characters that join, select or
// fill and render as nothing (U+034F, the Hangul fillers, variation selectors
// U+FE00-U+FE0E and U+E0100-U+E01EF), and the whole Tag block U+E0000-U+E007F
// (\p{Cf} leaves its unassigned code points). ZWNJ and ZWJ (U+200C, U+200D) stay, for
// emoji sequences and Persian or Indic text, and so does U+FE0F, which makes an
// emoji an emoji. The server cleans an approved query the same way (clean_query
// in server/chat/web_consent.py).
const BREAKS = /[\t\n\r\u0085\u2028\u2029]+/g;
// The combining ones come first in the class: a combining character after a plain one reads as a mistake to the linter.
// eslint-disable-next-line no-control-regex
const HIDDEN = /(?![\u200c\u200d])\p{Cf}|[\u034f\ufe00-\ufe0e\u{e0100}-\u{e01ef}\u{e0000}-\u{e007f}\u0000-\u001f\u007f-\u009f\u115f\u1160\u3164\uffa0]/gu;
const plain = (value) => (typeof value === 'string' ? value.replace(BREAKS, ' ').replace(HIDDEN, '') : '');

// The query as the box holds it: cleaned, and cut to what the box (and the
// server) allow. maxLength counts UTF-16 units, so a cut must not leave half
// of an emoji behind.
function queryText(query) {
    const text = plain(query);
    if (text.length <= MAX_QUERY) return text;
    const cut = text.slice(0, MAX_QUERY);
    return /[\ud800-\udbff]$/.test(cut) ? cut.slice(0, -1) : cut;
}

function shownReason(reason) {
    const letters = [...plain(reason).trim()];   // by character, so an emoji is never cut in half
    return letters.length > MAX_REASON ? `${letters.slice(0, MAX_REASON).join('')}…` : letters.join('');
}

/**
 * The question "search the web?" (C2.1 spec §5.8, §6): the model asked for a
 * search it isn't allowed to run, and this card is where the user says yes
 * (with the query they can edit) or "Not now".
 *
 * `reason` and `query` come from the saved offer note: raw model text, shown
 * as text. `onSearch(query)` gets the trimmed query. `disabled` (a reply is
 * streaming) stops the search, by button or by Enter, but not "Not now".
 * "Not now" hides the card in this component's state only and calls
 * `onDismiss`; nothing is sent or saved, so a fresh card (a reload) asks again.
 */
export default function WebOfferCard({ theme, reason, query, onSearch, onDismiss, disabled = false }) {
    const [dismissed, setDismissed] = useState(false);
    const [text, setText] = useState(() => queryText(query));
    if (dismissed) return null;

    const trimmed = text.trim();
    const canSearch = !disabled && trimmed.length > 0;
    const search = () => { if (canSearch) onSearch?.(trimmed); };
    const dismiss = () => {
        setDismissed(true);
        onDismiss?.();
    };
    const why = shownReason(reason);

    return (
        <div
            role="region"
            aria-label="Web search offer"
            data-own-keys
            className={`w-[22rem] max-w-full flex flex-col gap-2 px-3 py-3 rounded-xl border ${theme.border} ${theme.bgSecondary}`}
        >
            <p className={`flex items-center gap-1.5 text-xs font-bold ${theme.text}`}>
                <Globe size={13} aria-hidden="true" className="text-blue-500 shrink-0" />
                Search the web?
            </p>
            {why && <p className={`text-xs leading-relaxed break-words ${theme.textSecondary}`}>{why}</p>}
            <input
                type="text"
                value={text}
                maxLength={MAX_QUERY}
                aria-label="Web search query"
                onChange={(e) => setText(queryText(e.target.value))}
                onKeyDown={(e) => {
                    // Enter that picks an input-method candidate is not a request to search.
                    if (e.key !== 'Enter' || e.nativeEvent.isComposing) return;
                    e.preventDefault();
                    search();
                }}
                className={`w-full min-w-0 p-2 rounded-lg border ${theme.border} ${theme.bgTertiary} ${theme.text} text-xs outline-none focus:ring-2 focus:ring-blue-500 transition-colors`}
            />
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    onClick={search}
                    disabled={!canSearch}
                    className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                        canSearch
                            ? 'bg-blue-600 hover:bg-blue-700 text-white'
                            : `${theme.bgTertiary} ${theme.textMuted} opacity-50 cursor-not-allowed`
                    }`}
                >
                    Search the web
                </button>
                <button
                    type="button"
                    onClick={dismiss}
                    className={`px-3 py-1.5 rounded-lg border ${theme.border} text-xs font-bold ${theme.textSecondary} ${theme.hover} hover:text-blue-500 transition-colors`}
                >
                    Not now
                </button>
            </div>
        </div>
    );
}
