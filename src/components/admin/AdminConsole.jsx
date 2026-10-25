import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { DeploymentSection } from './DeploymentSection';
import { AssistantProfileSection } from './AssistantProfileSection';
import { ChatOptionsSection } from './ChatOptionsSection';
import { DefaultsSection } from './DefaultsSection';
import { DocumentsSection } from './DocumentsSection';
import { AdminProjectsSection } from './AdminProjectsSection';
import { OverviewSection } from './OverviewSection';
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
  { id: 'overview', label: 'Overview' },
  { id: 'people', label: 'People' },
  { id: 'projects', label: 'Projects' },
  { id: 'documents', label: 'Documents' },
  { id: 'chat', label: 'Chat' },
  { id: 'defaults', label: 'Defaults for users' },
  { id: 'usage', label: 'Usage' },
  { id: 'deployment', label: 'Deployment' },
];

export function AdminConsole({ theme, apiHost, apiPort, currentUserId, onBack, showToast }) {
  const [active, setActive] = useState('overview');
  // A section that edits reports unsaved changes through onDirtyChange;
  // every completed navigation starts clean.
  const [dirty, setDirty] = useState(false);
  const [openProjectId, setOpenProjectId] = useState(null);
  const [previewVersion, setPreviewVersion] = useState(0);  // bumped when a chat option changed what the assistant's rules say

  // The deployment's timezone, loaded once: People shows dates in it. A Save in Deployment updates it.
  const [zone, setZone] = useState('UTC');
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/settings/deployment');
        if (!res.ok) return;
        const value = (await res.json()).settings?.timezone?.value;
        if (!cancelled && typeof value === 'string' && value) setZone(value);
      } catch { /* dates then read in UTC, which is also the server's default */ }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort]);

  const select = (id) => { setActive(id); setDirty(false); };
  const sectionLabel = (id) => SECTIONS.find((s) => s.id === id)?.label ?? id;
  const openRef = useRef(null);   // the shell's tab-click path, for Overview's Open links

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
    <ConsoleShell theme={theme} sections={SECTIONS} active={active} onSelect={select} onBack={onBack} dirty={dirty} openRef={openRef}>
      {active === 'overview' && (
        <OverviewSection theme={theme} apiHost={apiHost} apiPort={apiPort} sectionLabel={sectionLabel}
          onOpenSection={(id) => openRef.current(id)} />
      )}
      {active === 'people' && (
        <PeopleSection theme={theme} apiHost={apiHost} apiPort={apiPort} currentUserId={currentUserId} showToast={showToast}
          zone={zone} />
      )}
      {active === 'projects' && (
        <AdminProjectsSection theme={theme} apiHost={apiHost} apiPort={apiPort} currentUserId={currentUserId}
          showToast={showToast} onOpenProject={setOpenProjectId} />
      )}
      {active === 'documents' && (
        <DocumentsSection theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast}
          onDirtyChange={setDirty} />
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
      {active === 'deployment' && (
        <DeploymentSection theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast}
          onDirtyChange={setDirty} onSaved={setZone} />
      )}
    </ConsoleShell>
  );
}
