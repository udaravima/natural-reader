import { roleLabel } from '../../lib/projectRoles';

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// The Library's Projects tab (A0 §10): one card per project I belong to.
export default function ProjectsTab({ theme, projects, onOpenProject }) {
  if (projects === null) return <p className={`text-xs ${theme.textMuted}`}>Loading…</p>;
  if (projects.length === 0) {
    return <p className={`text-xs ${theme.textMuted}`}>No projects yet. Create one with New project.</p>;
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {projects.map((p) => (
        <button
          key={p.id}
          onClick={() => onOpenProject(p.id)}
          aria-label={`Open project ${p.name}`}
          className={`flex flex-col gap-1 p-3 text-left rounded-lg border ${theme.border} ${theme.bgSecondary} hover:border-blue-500`}
        >
          <span className="flex items-center justify-between gap-2">
            <span className="text-xs font-bold truncate">{p.name}</span>
            {p.my_role && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">{roleLabel(p.my_role)}</span>
            )}
          </span>
          {p.description && <span className={`text-[10px] truncate ${theme.textSecondary}`}>{p.description}</span>}
          <span className={`text-[10px] ${theme.textMuted}`}>
            {plural(p.member_count, 'member')} · {plural(p.doc_count, 'document')}
          </span>
        </button>
      ))}
    </div>
  );
}
