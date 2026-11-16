import { useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { firstLocal, modelLabel } from './useChatModels';

const QUESTION_MAX = 500;   // characters

/**
 * Try it (character profiles spec, "Hear a sample reply"): the profile as it stands in the editor, unsaved, answers
 * one question with the chat model the admin picks, as a chat with no document would. The reply is shown here and
 * kept nowhere.
 */
export function TryProfile({ theme, apiHost, apiPort, showToast, models, name, backstory, text }) {
  const [question, setQuestion] = useState('Introduce yourself.');
  const [model, setModel] = useState(null);
  const [asking, setAsking] = useState(false);
  const [reply, setReply] = useState(null);
  const remembered = (() => {
    try { return JSON.parse(localStorage.getItem('neural-pdf-selectedModel') || 'null'); } catch { return null; }
  })();
  const chosen = model ?? (models.some((m) => m.id === remembered) ? remembered : firstLocal(models));
  const link = 'min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline';
  const box = `text-xs p-2 rounded border ${theme.border} ${theme.bg} ${theme.text}`;

  const ask = async () => {
    if (asking || !chosen || !question.trim() || !text.trim()) return;
    setAsking(true);
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant/sample', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, backstory, text, question, model: chosen }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast?.(body?.detail?.message || `Trying the profile failed (HTTP ${res.status}).`, 5000);
        return;
      }
      setReply(body.reply || '');
    } catch {
      showToast?.('The server could not be reached.', 5000);
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className={`flex flex-col gap-2 p-2 rounded border ${theme.border}`}>
      <span className={`text-xs font-bold ${theme.textSecondary}`}>Try it</span>
      <div className="flex flex-col md:flex-row gap-2">
        <input aria-label="Question" value={question} maxLength={QUESTION_MAX}
          onChange={(e) => setQuestion(e.target.value)} className={`min-h-11 md:min-h-0 flex-1 ${box}`} />
        <select aria-label="Answered by" value={chosen} onChange={(e) => setModel(e.target.value)}
          disabled={!models.length} className={`min-h-11 md:min-h-0 md:max-w-[14rem] ${box}`}>
          {models.map((m) => <option key={m.id} value={m.id}>{modelLabel(m)}</option>)}
        </select>
        <button onClick={ask} disabled={asking || !chosen || !question.trim() || !text.trim()}
          className={`shrink-0 text-xs font-bold ${link}`}>
          {asking ? 'Asking…' : 'Try it'}
        </button>
      </div>
      {reply !== null && (
        <p aria-label="Sample reply" className={`text-xs whitespace-pre-wrap p-2 rounded ${theme.bgSecondary} ${theme.text}`}>
          {reply}
        </p>
      )}
      <p className={`text-xs ${theme.textMuted}`}>
        The profile as it is in the editor answers once, with no documents. Nothing is saved, and the answer isn&apos;t kept.
      </p>
    </div>
  );
}
