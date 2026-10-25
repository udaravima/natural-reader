import { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { AssistantProfileSection } from './AssistantProfileSection';
import { ChatOptionsSection } from './ChatOptionsSection';
import { DefaultsSection } from './DefaultsSection';
import { AdminProjectsSection } from './AdminProjectsSection';
import { PeopleSection } from './PeopleSection';
import { UsageSection } from './UsageSection';
import { ConsoleShell } from './ConsoleShell';
import ProjectPage from '../projects/ProjectPage';

/**
 * Full-height admin console (spec §3): a section list and one section at a
 * time, shaped like the Settings window. Visual language mirrors
 * AdminPanel/AccountPanel (tiny text, list rows, underline buttons).
 *
 * All rails are server-side; this UI only hides/disables affordances
 * cosmetically (self-row hides Disable / Delete and disables the admin
 * capability checkbox, so an admin can't lock themselves out).
 */

const SECTIONS = [
  { id: 'people', label: 'People' },
  { id: 'projects', label: 'Projects' },
  { id: 'chat', label: 'Chat' },
  { id: 'defaults', label: 'Defaults for users' },
  { id: 'usage', label: 'Usage' },
  { id: 'deployment', label: 'Deployment' },
];

function fmtBudget(budget) {
  if (budget === null || budget === undefined) return 'unlimited (unset)';
  if (budget === 0) return 'unlimited';
  return `${budget} tokens/day`;
}

// Today's read-only "Deployment config" block, moved as it was (Task 10
// replaces it). It loads when the section opens.
function DeploymentBlock({ theme, apiHost, apiPort }) {
  const [config, setConfig] = useState(null);
  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';

  // Load via async IIFE so setState runs after the await, not synchronously
  // in the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/inference/config');
        if (!cancelled && res.ok) setConfig(await res.json());
      } catch { /* config is read-only garnish; failures are non-fatal */ }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort]);

  return (
        <section className="flex flex-col gap-3" aria-label="Deployment config">
          <h2 className={sectionTitle}>Deployment config</h2>
          {config === null ? (
            <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
          ) : (
            <div className={`flex flex-col gap-1 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary} text-xs`}>
              {Array.isArray(config.providers) && (
                <div className="flex justify-between gap-4">
                  <span className={`${theme.textMuted}`}>Model providers</span>
                  <ul className="text-right">
                    {config.providers.length === 0 ? <li>none configured</li> : config.providers.map(p => (
                      <li key={p.name}>
                        {p.name} · {p.kind} · {p.url_host ?? 'unknown host'} · {p.models === null ? 'unreachable' : `${p.models} model${p.models === 1 ? '' : 's'}`}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Embeddings (Ollama) URL</span><span className="truncate">{config.ollama_url}</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Chat timeout</span><span>{config.timeout_s}s</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Allowed models</span><span className="text-right">{config.allowed_models ? config.allowed_models.join(', ') : 'all models allowed'}</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Summarize model</span><span>{config.summarize_model ?? '—'}</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Embed model</span><span>{config.embed_model ?? '—'}</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Default daily budget</span><span>{fmtBudget(config.daily_token_budget)}</span></div>
            </div>
          )}
        </section>
  );
}

export function AdminConsole({ theme, apiHost, apiPort, currentUserId, onBack, showToast }) {
  const [active, setActive] = useState('people');
  // A section that edits reports unsaved changes through onDirtyChange
  // (none does yet); every completed navigation starts clean.
  const [dirty, setDirty] = useState(false);
  const [openProjectId, setOpenProjectId] = useState(null);
  const [previewVersion, setPreviewVersion] = useState(0);  // bumped when a chat option changed what the assistant's rules say

  const select = (id) => { setActive(id); setDirty(false); };

  if (openProjectId) {
    return (
      <div className={`h-full w-full overflow-y-auto ${theme.bg} ${theme.text}`}>
        <div className="max-w-4xl mx-auto px-4 md:px-8 py-6">
          <ProjectPage theme={theme} apiHost={apiHost} apiPort={apiPort} projectId={openProjectId}
            currentUserId={currentUserId} showToast={showToast} onBack={() => setOpenProjectId(null)} />
        </div>
      </div>
    );
  }

  return (
    <ConsoleShell theme={theme} sections={SECTIONS} active={active} onSelect={select} onBack={onBack} dirty={dirty}>
      {active === 'people' && (
        <PeopleSection theme={theme} apiHost={apiHost} apiPort={apiPort} currentUserId={currentUserId} showToast={showToast} />
      )}
      {active === 'projects' && (
        <AdminProjectsSection theme={theme} apiHost={apiHost} apiPort={apiPort} currentUserId={currentUserId}
          showToast={showToast} onOpenProject={setOpenProjectId} />
      )}
      {active === 'chat' && (
        <>
          <AssistantProfileSection theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast}
            refreshKey={previewVersion} />
          <ChatOptionsSection theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast}
            onSaved={() => setPreviewVersion((v) => v + 1)} />
        </>
      )}
      {active === 'defaults' && (
        <DefaultsSection theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast} />
      )}
      {active === 'usage' && <UsageSection theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast} />}
      {active === 'deployment' && <DeploymentBlock theme={theme} apiHost={apiHost} apiPort={apiPort} />}
    </ConsoleShell>
  );
}
