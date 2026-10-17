import { MessageSquarePlus, Maximize2, X } from 'lucide-react';
import { groupByProvider, modelLabel } from '../../lib/modelIds';

/**
 * Chat beside the page, in the reader: the same chat as the Chat view (one
 * chat engine, one draft), narrowed to a column so the page and the
 * narration stay in view. `children` is the ChatView to show. The model
 * picker sits here because the chat sidebar that normally holds it isn't on
 * screen.
 */
export default function ChatSidePanel({
    theme, title, selectedModel, setSelectedModel, availableModels,
    onNewChat, onExpand, onClose, children,
}) {
    return (
        <aside
            aria-label="Chat beside the page"
            className={`w-[400px] xl:w-[440px] shrink-0 flex flex-col border-l ${theme.border} ${theme.bgSecondary}`}
        >
            <div className={`flex items-center gap-2 px-3 py-2 border-b ${theme.border}`}>
                <span className={`flex-1 min-w-0 truncate text-xs font-bold ${theme.text}`} title={title}>{title}</span>
                <button onClick={onNewChat} title="New chat" aria-label="New chat"
                    className={`p-1.5 rounded-lg ${theme.hover} ${theme.textSecondary} hover:text-blue-500`}>
                    <MessageSquarePlus size={15} />
                </button>
                <button onClick={onExpand} title="Open in the Chat view" aria-label="Open in the Chat view"
                    className={`p-1.5 rounded-lg ${theme.hover} ${theme.textSecondary} hover:text-blue-500`}>
                    <Maximize2 size={15} />
                </button>
                <button onClick={onClose} title="Close the chat" aria-label="Close the chat"
                    className={`p-1.5 rounded-lg ${theme.hover} ${theme.textSecondary} hover:text-red-500`}>
                    <X size={15} />
                </button>
            </div>
            <div className={`px-3 py-1.5 border-b ${theme.border}`}>
                <select
                    aria-label="Model"
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
                    disabled={availableModels.length === 0}
                    className={`w-full text-[11px] font-bold p-1.5 rounded-lg border ${theme.border} ${theme.bgTertiary} ${theme.text} outline-none focus:ring-2 focus:ring-blue-500 ${availableModels.length === 0 ? 'opacity-60' : ''}`}
                >
                    {availableModels.length === 0 ? (
                        <option value="">No models found</option>
                    ) : (
                        <>
                            {!selectedModel && <option value="">Select a model…</option>}
                            {selectedModel && !availableModels.some((m) => m.id === selectedModel) && (
                                <option value={selectedModel} disabled>{selectedModel} (unavailable)</option>
                            )}
                            {groupByProvider(availableModels).map(([provider, models]) => (
                                <optgroup key={provider} label={provider}>
                                    {models.map((m) => <option key={m.id} value={m.id}>{modelLabel(m)}</option>)}
                                </optgroup>
                            ))}
                        </>
                    )}
                </select>
            </div>
            <div className="flex-1 min-h-0 flex flex-col">{children}</div>
        </aside>
    );
}
