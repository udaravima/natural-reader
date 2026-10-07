import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Pencil, Loader2 } from 'lucide-react';
import { projectsApi } from '../../lib/projectsApi';
import { roleLabel } from '../../lib/projectRoles';
import { useLatest } from '../../hooks/useLatest';
import ProjectDocsTab from './ProjectDocsTab';
import MembersTab from './MembersTab';
import ActivityTab from './ActivityTab';

const TABS = [['documents', 'Documents'], ['members', 'Members'], ['activity', 'Activity']];

/**
 * One project (A0 §10): header (name, description, my role, Edit / Leave /
 * Delete as `can` allows) and Documents | Members | Activity tabs. Any
 * refusal reloads the project, so controls the server no longer allows
 * disappear; a project I can no longer see sends me back with the notice.
 */
export default function ProjectPage({ theme, apiHost, apiPort, projectId, currentUserId, showToast, onBack, onChanged, onOpenDoc }) {
  const api = useMemo(() => projectsApi(apiHost, apiPort), [apiHost, apiPort]);
  const [project, setProject] = useState(null);
  const [tab, setTab] = useState('documents');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ name: '', description: '' });
  const [confirm, setConfirm] = useState(null); // 'leave' | 'delete'
  const [busy, setBusy] = useState(false);
  const cb = useLatest({ showToast, onBack, onChanged });

  const reload = useCallback(async () => {
    try {
      const fresh = await api.get(projectId);
      setProject(fresh);
      if (!fresh.can.edit) setEditing(false); // lost the right to edit: leave the form
    } catch (e) {
      cb.current.showToast(e.message, 5000);
      cb.current.onBack();
    }
  }, [api, projectId, cb]);

  useEffect(() => { reload(); }, [reload]);

  const act = async (fn) => {
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      showToast(e.message, 5000);
      await reload();
      return undefined;
    } finally {
      setBusy(false);
    }
  };

  if (!project) return <p className={`text-xs ${theme.textMuted}`}>Loading…</p>;
  const { can } = project;

  const startEdit = () => { setDraft({ name: project.name, description: project.description || '' }); setEditing(true); };
  const save = (e) => {
    e.preventDefault();
    act(async () => {
      const saved = await api.update(project.id, { name: draft.name.trim(), description: draft.description.trim() || null });
      setProject(saved);
      setEditing(false);
      onChanged?.();
    });
  };
  const leave = () => act(async () => {
    await api.removeMember(project.id, currentUserId);
    showToast(`You left ${project.name}.`, 3000);
    onChanged?.();
    onBack();
  });
  const remove = () => act(async () => {
    await api.remove(project.id);
    showToast(`Deleted ${project.name}.`, 3000);
    onChanged?.();
    onBack();
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1 min-w-0 flex-1">
          <button onClick={onBack} className="self-start text-xs underline text-blue-500 flex items-center gap-1">
            <ArrowLeft size={12} /> Projects
          </button>
          {editing ? (
            <form onSubmit={save} className="flex flex-col gap-2">
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} maxLength={200}
                aria-label="Project name" disabled={busy}
                className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`} />
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                aria-label="Project description" placeholder="Description (optional)" disabled={busy}
                className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`} />
              <span className="flex gap-2">
                <button type="submit" disabled={busy || !draft.name.trim()}
                  className="px-2 py-1 text-xs rounded bg-blue-500 text-white disabled:opacity-50">
                  {busy ? <Loader2 size={12} className="animate-spin" /> : 'Save'}
                </button>
                <button type="button" onClick={() => setEditing(false)} className="text-xs underline">Cancel</button>
              </span>
            </form>
          ) : (
            <>
              <h2 className="text-base font-bold flex items-center gap-2">
                <span className="truncate">{project.name}</span>
                {project.my_role && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">{roleLabel(project.my_role)}</span>
                )}
              </h2>
              {project.description && <p className={`text-xs ${theme.textSecondary}`}>{project.description}</p>}
            </>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0 text-xs">
          {can.edit && !editing && (
            <button onClick={startEdit} aria-label="Edit project" className="flex items-center gap-1 underline">
              <Pencil size={12} /> Edit
            </button>
          )}
          {can.leave && <button onClick={() => setConfirm('leave')} aria-label="Leave project" className="underline">Leave</button>}
          {can.delete && (
            <button onClick={() => setConfirm('delete')} aria-label="Delete project" className="underline text-red-500">Delete project</button>
          )}
        </div>
      </div>

      {confirm && (
        <div className={`flex flex-wrap items-center gap-2 p-2 text-[11px] rounded border ${theme.border}`}>
          <span className={theme.textSecondary}>
            {confirm === 'leave'
              ? `Leave ${project.name}? You lose access to its documents unless they're in your library.`
              : `Delete ${project.name}? Members lose access. Documents stay in the libraries of people who hold them; a document only this project held is deleted.`}
          </span>
          <button onClick={confirm === 'leave' ? leave : remove} disabled={busy} className="underline text-red-500">
            {confirm === 'leave' ? 'Confirm leave' : 'Confirm delete'}
          </button>
          <button onClick={() => setConfirm(null)} className="underline">Cancel</button>
        </div>
      )}

      <div role="tablist" aria-label="Project sections" className={`flex gap-4 border-b ${theme.border}`}>
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
            className={`pb-1 text-xs ${tab === key ? 'border-b-2 border-blue-500 text-blue-500' : theme.textSecondary}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'documents' && (
        <ProjectDocsTab theme={theme} api={api} project={project} showToast={showToast} onOpenDoc={onOpenDoc}
          onRefused={reload} onChanged={reload} />
      )}
      {tab === 'members' && (
        <MembersTab theme={theme} api={api} project={project} currentUserId={currentUserId} showToast={showToast}
          onRefused={reload} onChanged={reload} />
      )}
      {tab === 'activity' && <ActivityTab theme={theme} api={api} project={project} />}
    </div>
  );
}
