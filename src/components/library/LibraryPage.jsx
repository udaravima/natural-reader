import { useCallback, useEffect, useRef, useState } from 'react';
import { Search, FolderOpen, Share2, X, Check, Trash2, Loader2 } from 'lucide-react';
import { apiFetch } from '../../utils/apiFetch';

// Typing pauses this long before a search-as-you-type request fires. Keeps
// GET /v1/docs?q=... from firing on every keystroke while staying fast
// enough that it reads as instant.
const SEARCH_DEBOUNCE_MS = 300;

function buildDocsPath({ q, projectId }) {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (projectId) params.set('project_id', projectId);
  const qs = params.toString();
  return qs ? `/v1/docs?${qs}` : '/v1/docs';
}

// Inline tag editor for a row the caller owns. Adding or removing a chip
// PATCHes the whole replacement array in one shot — the backend
// dedupes/sorts it server-side, so the client doesn't need to.
function TagEditor({ doc, theme, onSave }) {
  const [draft, setDraft] = useState('');
  const tags = doc.tags || [];

  const addTag = () => {
    const t = draft.trim();
    if (!t || tags.includes(t)) { setDraft(''); return; }
    onSave([...tags, t]);
    setDraft('');
  };
  const removeTag = (t) => onSave(tags.filter((x) => x !== t));

  return (
    <div className="flex flex-wrap items-center gap-1">
      {tags.map((t) => (
        <span
          key={t}
          className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] ${theme.bgTertiary} ${theme.textSecondary}`}
        >
          {t}
          <button onClick={() => removeTag(t)} aria-label={`Remove tag ${t}`} className="hover:text-red-500">
            <X size={10} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }}
        placeholder="add tag"
        aria-label={`Add tag to ${doc.file_name}`}
        className={`px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} w-20`}
      />
      <button
        onClick={addTag}
        aria-label={`Confirm add tag to ${doc.file_name}`}
        className={`hover:text-blue-500 ${theme.textSecondary}`}
      >
        <Check size={10} />
      </button>
    </div>
  );
}

// One chip per linked project. The owner of the doc can add it to any
// project they can see and remove it from any; a project owner can remove
// someone else's doc from THEIR project (never add it — spec §6).
function ProjectChips({ doc, projects, theme, onLink, onUnlink }) {
  const linked = doc.projects || [];
  const linkedIds = new Set(linked.map((p) => p.id));
  const ownedProjectIds = new Set((projects || []).filter((p) => p.is_owner).map((p) => p.id));
  const addable = (projects || []).filter((p) => !linkedIds.has(p.id));

  return (
    <div className="flex flex-wrap items-center gap-1">
      <FolderOpen size={10} className={theme.textSecondary} />
      {linked.length === 0 && <span className={theme.textSecondary}>No project</span>}
      {linked.map((p) => (
        <span
          key={p.id}
          className={`flex items-center gap-1 px-1.5 py-0.5 rounded ${theme.bgTertiary} ${theme.textSecondary}`}
        >
          {p.name}
          {(doc.is_owner || ownedProjectIds.has(p.id)) && (
            <button
              onClick={() => onUnlink(doc, p)}
              aria-label={`Remove ${doc.file_name} from ${p.name}`}
              className="hover:text-red-500"
            >
              <X size={10} />
            </button>
          )}
        </span>
      ))}
      {doc.is_owner && addable.length > 0 && (
        <select
          value=""
          onChange={(e) => { if (e.target.value) onLink(doc, e.target.value); }}
          aria-label={`Add ${doc.file_name} to project`}
          className={`px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg}`}
        >
          <option value="">+ project</option>
          {addable.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      )}
    </div>
  );
}

/**
 * Library: a flat list/search/filter view over every document the caller
 * can read (own docs, project-member docs, explicitly-granted docs — the
 * split is resolved server-side via readable_docs_where and just arrives
 * here as `is_owner`). No chat wiring — that's Phase 1. Owner rows get
 * inline tag editing and project chips (add/remove); project owners can also
 * remove others' docs from their projects; shared rows are read-only and
 * carry a "shared" badge instead.
 */
export default function LibraryPage({ theme, apiHost, apiPort, showToast }) {
  const [docs, setDocs] = useState(null);
  const [projects, setProjects] = useState(null);
  const [search, setSearch] = useState('');
  const [projectFilter, setProjectFilter] = useState('');
  const [deleteTarget, setDeleteTarget] = useState(null);
  // doc_id whose DELETE is currently in flight — drives the busy state on the
  // confirm button so a click clearly registers (and can't be double-fired).
  const [deletingId, setDeletingId] = useState(null);
  // Monotonic id so out-of-order responses can't clobber the list: fast typing
  // across debounce windows can leave two GET /v1/docs in flight, and if the
  // earlier one resolves last its stale results would overwrite the newer query.
  const reqIdRef = useRef(0);

  const loadDocs = useCallback(async (q, projectId) => {
    const reqId = ++reqIdRef.current;
    try {
      const res = await apiFetch(apiHost, apiPort, buildDocsPath({ q, projectId }));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (reqId === reqIdRef.current) setDocs(data);
    } catch (e) {
      if (reqId !== reqIdRef.current) return; // a newer request superseded this one
      setDocs([]);
      showToast(`Could not load documents: ${e.message}`, 5000);
    }
  }, [apiHost, apiPort, showToast]);

  const loadProjects = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/projects');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setProjects(await res.json());
    } catch (e) {
      setProjects([]);
      showToast(`Could not load projects: ${e.message}`, 5000);
    }
  }, [apiHost, apiPort, showToast]);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  // Debounced search-as-you-type: an empty query fires immediately (initial
  // load, or clearing the box); a non-empty one waits out the debounce.
  useEffect(() => {
    const delay = search ? SEARCH_DEBOUNCE_MS : 0;
    const t = setTimeout(() => loadDocs(search, projectFilter), delay);
    return () => clearTimeout(t);
  }, [search, projectFilter, loadDocs]);

  const patchDoc = async (doc, body) => {
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/docs/${doc.doc_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadDocs(search, projectFilter);
    } catch (e) {
      showToast(`Update failed: ${e.message}`, 5000);
    }
  };

  const changeLink = async (doc, projectId, method) => {
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/projects/${projectId}/docs/${doc.doc_id}`, { method });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadDocs(search, projectFilter);
    } catch (e) {
      showToast(`Update failed: ${e.message}`, 5000);
    }
  };
  const linkDoc = (doc, projectId) => changeLink(doc, projectId, 'PUT');
  const unlinkDoc = (doc, project) => changeLink(doc, project.id, 'DELETE');

  const deleteDoc = async (doc) => {
    setDeletingId(doc.doc_id);
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/docs/${doc.doc_id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDeleteTarget(null);
      await loadDocs(search, projectFilter);
    } catch (e) {
      showToast(`Delete failed: ${e.message}`, 5000);
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className={`h-full w-full overflow-y-auto ${theme.bg} ${theme.text}`}>
      <div className="max-w-4xl mx-auto px-4 md:px-8 py-6 flex flex-col gap-6">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold flex items-center gap-2">
            <FolderOpen size={18} className="text-blue-500" />
            Library
          </h1>
        </div>

        {/* Search + project filter */}
        <div className="flex flex-wrap items-center gap-2">
          <div className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border ${theme.border} ${theme.bgTertiary} flex-1 min-w-[200px]`}>
            <Search size={14} className={theme.textSecondary} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search documents…"
              aria-label="Search documents"
              className={`flex-1 bg-transparent text-xs outline-none ${theme.text}`}
            />
          </div>
          <select
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            aria-label="Filter by project"
            className={`px-2 py-1.5 text-xs rounded-lg border ${theme.border} ${theme.bg}`}
          >
            <option value="">All projects</option>
            {(projects || []).map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>

        {/* Document rows */}
        {docs === null ? (
          <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
        ) : docs.length === 0 ? (
          <p className={`text-xs ${theme.textMuted}`}>No documents found.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {docs.map((doc) => {
              const deleting = deleteTarget === doc.doc_id;
              return (
                <div
                  key={doc.doc_id}
                  data-testid={`doc-row-${doc.doc_id}`}
                  className={`flex flex-col gap-2 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-xs font-bold truncate">{doc.file_name}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${theme.bgTertiary} ${theme.textSecondary}`}>
                        {doc.state}
                      </span>
                      {!doc.is_owner && (
                        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">
                          <Share2 size={10} /> shared
                        </span>
                      )}
                    </div>
                    {doc.is_owner && (
                      deleting ? (
                        <span className="flex items-center gap-1 shrink-0">
                          {deletingId === doc.doc_id ? (
                            <button
                              disabled
                              className="flex items-center gap-1 text-[10px] text-red-500 cursor-default"
                            >
                              <Loader2 size={10} className="animate-spin" /> Deleting…
                            </button>
                          ) : (
                            <>
                              <button onClick={() => deleteDoc(doc)} className="text-[10px] underline text-red-500">
                                Confirm delete
                              </button>
                              <button onClick={() => setDeleteTarget(null)} className="text-[10px] underline">
                                Cancel
                              </button>
                            </>
                          )}
                        </span>
                      ) : (
                        <button
                          onClick={() => setDeleteTarget(doc.doc_id)}
                          aria-label={`Delete ${doc.file_name}`}
                          className={`shrink-0 hover:text-red-500 ${theme.textSecondary}`}
                        >
                          <Trash2 size={12} />
                        </button>
                      )
                    )}
                  </div>

                  <div className="text-[10px]">
                    <ProjectChips
                      doc={doc}
                      projects={projects}
                      theme={theme}
                      onLink={linkDoc}
                      onUnlink={unlinkDoc}
                    />
                  </div>

                  {doc.is_owner ? (
                    <TagEditor doc={doc} theme={theme} onSave={(tags) => patchDoc(doc, { tags })} />
                  ) : (
                    (doc.tags || []).length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {doc.tags.map((t) => (
                          <span key={t} className={`px-1.5 py-0.5 rounded text-[10px] ${theme.bgTertiary} ${theme.textSecondary}`}>
                            {t}
                          </span>
                        ))}
                      </div>
                    )
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
