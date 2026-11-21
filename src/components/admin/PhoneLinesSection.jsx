import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiFetch } from '../../utils/apiFetch';
import { phoneApi } from '../../lib/phoneApi';
import { projectsApi } from '../../lib/projectsApi';
import { formatMoment } from '../../lib/zoneFormat';
import { SearchableSelect } from '../SearchableList';
import { firstLocal, modelLabel, useChatModels } from './useChatModels';

/**
 * Admin console: phone lines (phone lines spec §5.8). A list of lines with the listener's state, and an editor for
 * one: who answers (profile), what it knows (project), the model, the voice and the welcome, then the dialplan
 * snippet for the phone system and the line's calls with their transcripts.
 *
 * It reuses what the console already has: the assistant profiles (Chat), the projects (Projects, only those the
 * admin maintains: putting a project on a line is a maintainer's act), the chat models (Models), and the picker
 * look of ChatVoicePicker. Voices come from the phone API's own list, which holds only the voices a line can use.
 */

const WARNING = 'Anyone who calls this line hears answers drawn from this project.';
const LISTENER_OFF = 'The phone listener is off. Set PHONE_AUDIOSOCKET_LISTEN and PHONE_AUDIOSOCKET_ALLOW, then restart.';
const NO_PBX_ADDRESS = 'Set PHONE_PBX_ADDRESS to the address your phone system dials, then restart.';
const SNIPPET_HINT = "Pick any free extension. The id is this line's.";
const welcomeFor = (name) => `Hello, you've reached ${name.trim() || 'this line'}. I'm an automated assistant. How can I help?`;

// Why a call ended (phone_calls.end_reason); null means it hasn't.
const END_REASONS = {
  caller: 'caller hung up', limit: 'reached the time limit', idle: 'no one spoke', budget: "line's token budget was used up",
  busy: 'line was busy', closed: 'line was closed', error: 'something went wrong', shutdown: 'app was restarted',
};

/** The form's limits, as the API's (maxCallS 60-3600 s, maxCalls 1-20, dailyTokens 0 and up). The first problem, or null. */
function problemOf(f) {
  const whole = (text) => text.trim() !== '' && Number.isInteger(Number(text));
  // Minutes may have a fraction (90 s is 1.5); the API's limit is 60 to 3600 seconds.
  const seconds = Math.round(Number(f.maxCallMin) * 60);
  if (f.maxCallMin.trim() === '' || !Number.isFinite(seconds) || seconds < 60 || seconds > 3600) {
    return 'Longest call must be 1 to 60 minutes (a fraction such as 1.5 is fine).';
  }
  if (!whole(f.maxCalls) || Number(f.maxCalls) < 1 || Number(f.maxCalls) > 20) return 'Calls at once must be a whole number, 1 to 20.';
  if (f.dailyTokens.trim() !== '' && (!whole(f.dailyTokens) || Number(f.dailyTokens) < 0 || !Number.isSafeInteger(Number(f.dailyTokens)))) {
    return 'Tokens a day must be a whole number, 0 or more, or empty for the default.';
  }
  return null;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function lengthText(seconds) {
  if (seconds == null) return 'in progress';
  const s = Math.round(seconds);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} m ${s % 60} s`;
}

/** The editor's fields for a saved line. Minutes and tokens are text while they are typed. */
function formOf(line) {
  return {
    name: line.name, profileId: line.profile?.id ?? '', projectId: line.project?.id ?? '', model: line.model,
    engine: line.voice.engine, voice: line.voice.voice, accent: line.voice.accent ?? '',
    greeting: line.greeting, greetingTouched: true, maxCallMin: String(line.maxCallS / 60), maxCalls: String(line.maxCalls),
    dailyTokens: line.dailyTokens == null ? '' : String(line.dailyTokens), enabled: line.enabled,
  };
}

/** Whether an accent belongs with an engine: Google's two have one, Kokoro doesn't. */
const voiceOf = (f) => ({ engine: f.engine, voice: f.voice, ...(f.accent && f.engine !== 'kokoro' ? { accent: f.accent } : {}) });
const sameVoice = (a, b) => a.engine === b.engine && a.voice === b.voice && (a.accent ?? '') === (b.accent ?? '');

/** The whole line as a create body. */
function createBody(f) {
  return {
    name: f.name.trim(), enabled: f.enabled, profileId: f.profileId || null, projectId: f.projectId || null, model: f.model,
    voice: voiceOf(f), greeting: f.greeting.trim(), maxCallS: Math.round(Number(f.maxCallMin) * 60),
    maxCalls: Number(f.maxCalls), dailyTokens: f.dailyTokens === '' ? null : Number(f.dailyTokens),
  };
}

/** Only what differs from the saved line: a save never re-sends (or re-renders) what didn't change. */
function patchBody(f, line) {
  const next = createBody(f);
  const was = createBody(formOf(line));
  const out = {};
  for (const key of ['name', 'enabled', 'profileId', 'projectId', 'model', 'greeting', 'maxCallS', 'maxCalls', 'dailyTokens']) {
    if (next[key] !== was[key]) out[key] = next[key];
  }
  if (!sameVoice(next.voice, was.voice)) out.voice = next.voice;
  return out;
}

export function PhoneLinesSection({ theme, apiHost, apiPort, showToast, onDirtyChange, zone = 'UTC' }) {
  const api = useMemo(() => phoneApi(apiHost, apiPort), [apiHost, apiPort]);
  const models = useChatModels(apiHost, apiPort);
  const [lines, setLines] = useState(null);       // null while loading
  const [loadError, setLoadError] = useState(null);
  const [status, setStatus] = useState(null);     // the listener: { listening, pbx_address, active }
  const [voices, setVoices] = useState([]);       // [{ engine, label, voices, accents? }]
  const [profiles, setProfiles] = useState([]);
  const [projects, setProjects] = useState([]);
  const [projectsLoaded, setProjectsLoaded] = useState(false);   // false while loading or after a failure
  const [engineNames, setEngineNames] = useState({});   // engine id -> display name, for one /voices leaves out
  const [editing, setEditing] = useState(null);   // null: the list; { line } a saved line; { line: null } a new one
  const [draft, setDraft] = useState(null);
  // A new line starts on the first local model, but the models may arrive after "New line" was pressed.
  const form = draft && !draft.model ? { ...draft, model: firstLocal(models) } : draft;
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);   // an Edit is reading its line
  const [problem, setProblem] = useState(null);     // the last refusal: { message, reason }, in the API's words
  const error = problem?.message ?? null;
  const setError = (e) => setProblem(e ? { message: e.message ?? e, reason: e.reason } : null);
  const [callsKey, setCallsKey] = useState(0);     // bumped to make the calls list read again

  const load = useCallback(async () => {
    try {
      setLines(await api.lines());
      setLoadError(null);
    } catch (e) {
      setLoadError(e.message);
    }
    try { setStatus(await api.status()); } catch { setStatus(null); }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  // The pickers' choices: read once. A failure leaves a picker short, never the section blank.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const v = await api.voices();
        if (!cancelled && Array.isArray(v?.engines)) setVoices(v.engines);
      } catch { /* the voice picker stays empty */ }
      try {
        // The chat voice's engine list names every engine, set up or not: it names a saved engine /voices left out.
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/chat-voice');
        const body = res.ok ? await res.json() : null;
        if (!cancelled && Array.isArray(body?.engines)) setEngineNames(Object.fromEntries(body.engines.map((e) => [e.id, e.label])));
      } catch { /* the raw id is shown instead */ }
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant/profiles');
        if (res.ok && !cancelled) setProfiles((await res.json()).profiles ?? []);
      } catch { /* the default profile is still offered */ }
      try {
        const mine = await projectsApi(apiHost, apiPort).list();
        if (!cancelled) {
          setProjects(mine.filter((p) => p.my_role === 'maintainer' || p.my_role === 'owner'));
          setProjectsLoaded(true);
        }
      } catch { /* no project can be picked */ }
    })();
    return () => { cancelled = true; };
  }, [api, apiHost, apiPort]);

  const saved = editing?.line ?? null;
  const dirty = form !== null && (saved ? Object.keys(patchBody(form, saved)).length > 0 : form.name.trim() !== '');
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  /** Replace a line in the list (and the open editor) with a fresher copy of it. */
  const adopt = (line) => {
    setLines((all) => (all ?? []).map((l) => (l.id === line.id ? line : l)));
    setEditing({ line });
  };

  const opening_ = useRef(null);   // what is being opened: a line id, or 'new'; a slower answer for another is dropped

  const open = async (listed) => {
    setError(null);
    let line = listed;
    if (line) {
      // The list was read when the section opened: read this line again, so what the editor shows is current.
      opening_.current = listed.id;
      setOpening(true);
      try {
        line = await api.getLine(listed.id);
      } catch (e) {
        if (opening_.current !== listed.id) return;
        opening_.current = null;
        setOpening(false);
        if (e.status === 404) { setError(e); load(); return; }
        setError(e);   // the network: the list's copy would be stale, so open nothing
        return;
      }
      if (opening_.current !== listed.id) return;   // another editor was opened meanwhile
      opening_.current = null;
      setOpening(false);
      adopt(line);
      setDraft(formOf(line));
    } else {
      opening_.current = 'new';
      const engine = voices[0];
      setDraft({
        name: '', profileId: '', projectId: '', model: firstLocal(models), engine: engine?.engine ?? 'kokoro',
        voice: engine?.voices[0] ?? '', accent: engine?.accents?.[0]?.code ?? '', greeting: welcomeFor(''),
        greetingTouched: false, maxCallMin: '10', maxCalls: '2', dailyTokens: '', enabled: false,
      });
    }
    setEditing({ line });
  };

  /** A call's transcript went: the line has one fewer. A functional update, so a save landing meanwhile isn't undone. */
  const fewerTranscripts = (n) => {
    const fewer = (l) => ({ ...l, transcripts: Math.max(0, l.transcripts - n) });
    setLines((all) => (all ?? []).map((l) => (l.id === saved.id ? fewer(l) : l)));
    setEditing((cur) => (cur?.line?.id === saved.id ? { line: fewer(cur.line) } : cur));
  };

  /** "Load it again": the line as the server has it now, with the form made from it (R12.8.2). */
  const reload = async () => {
    setBusy(true);
    try {
      const line = await api.getLine(saved.id);
      adopt(line);
      setDraft(formOf(line));
      setCallsKey((k) => k + 1);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    if (dirty && !window.confirm('Discard the unsaved changes to this line?')) return;
    load();   // the list may have changed under the editor (calls today, access)
    setEditing(null);
    setDraft(null);
    setError(null);
  };

  const set = (patch) => setDraft((f) => ({ ...f, ...patch }));
  const setName = (name) => setDraft((f) => ({ ...f, name, ...(f.greetingTouched ? {} : { greeting: welcomeFor(name) }) }));
  const setEngine = (engine) => {
    const entry = voices.find((v) => v.engine === engine);
    if (!entry && saved && engine === saved.voice.engine) {
      // The saved engine isn't offered now, so there is no list to take a voice from: back to the saved one.
      set({ engine, voice: saved.voice.voice, accent: saved.voice.accent ?? '' });
      return;
    }
    set({ engine, voice: entry?.voices[0] ?? '', accent: entry?.accents ? (form.accent || entry.accents[0].code) : '' });
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const line = saved ? await api.updateLine(saved.id, patchBody(form, saved)) : await api.createLine(createBody(form));
      setLines((all) => (saved ? all.map((l) => (l.id === line.id ? line : l)) : [...(all ?? []), line]));
      setEditing({ line });
      setDraft(formOf(line));
      setCallsKey((k) => k + 1);
      showToast?.(`${line.name} saved.`, 4000);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      // The count in the question is read now, not when the section opened.
      let current = saved;
      try { current = await api.getLine(saved.id); adopt(current); } catch { /* ask with what is shown */ }
      if (!window.confirm(`Delete ${current.name} and its ${plural(current.transcripts, 'call transcript')}?`)) return;
      await api.deleteLine(saved.id);
      showToast?.(`${saved.name} deleted.`, 4000);
      setLines((all) => all.filter((l) => l.id !== saved.id));
      setEditing(null);
      setDraft(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const restoreAccess = async () => {
    setBusy(true);
    setError(null);
    try {
      const line = await api.restoreAccess(saved.id);
      setLines((all) => all.map((l) => (l.id === line.id ? line : l)));
      setEditing({ line });
      showToast?.(`${line.name} can read ${line.project?.name ?? 'its project'} again.`, 4000);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const heading = (
    <div className="flex items-center justify-between">
      <h2 className="text-xs font-bold uppercase tracking-wider text-blue-500">Phone lines</h2>
      {!editing && (
        <button type="button" onClick={() => open(null)} disabled={lines === null || opening}
          className="min-h-11 md:min-h-0 px-3 py-1 text-xs rounded border border-blue-500 text-blue-500 disabled:opacity-40">
          New line
        </button>
      )}
    </div>
  );

  return (
    <section className="flex flex-col gap-3" aria-label="Phone lines">
      {heading}
      {editing ? (
        <LineEditor
          theme={theme} api={api} saved={saved} form={form} set={set} setName={setName} setEngine={setEngine}
          voices={voices} profiles={profiles} projects={projects} models={models} status={status}
          busy={busy} dirty={dirty} error={error} zone={zone} showToast={showToast}
          changed={problem?.reason === 'line_changed'} callsKey={callsKey}
          onSave={save} onCancel={close} onDelete={remove} onRestore={restoreAccess} onReload={reload}
          onCallDeleted={fewerTranscripts} projectsLoaded={projectsLoaded} engineNames={engineNames}
        />
      ) : (
        <LineList theme={theme} lines={lines} status={status} loadError={loadError} zone={zone} onRetry={load} onEdit={open}
          opening={opening} error={error} />
      )}
    </section>
  );
}

/** The gate turns callers away from a line that is on but has lost its project or its access to it. */
const closedByGate = (l) => l.enabled && (!l.project || l.access === false);

function LineList({ theme, lines, status, loadError, zone, onRetry, onEdit, opening, error }) {
  return (
    <>
      {status && (
        <p className={`text-xs ${theme.textSecondary}`}>
          {!status.listening ? LISTENER_OFF
            : !status.pbx_address ? NO_PBX_ADDRESS
              : `Listening for your phone system at ${status.pbx_address} · ${plural(status.active, 'call')} now`}
        </p>
      )}
      {opening && <p role="status" className={`text-xs ${theme.textMuted}`}>Opening…</p>}
      {error && <p role="alert" className="text-xs text-red-500">{error}</p>}
      {loadError && (
        <p role="alert" className="text-xs text-red-500 flex items-center gap-2">
          Could not load the phone lines: {loadError}
          <button type="button" onClick={onRetry} className="min-h-11 md:min-h-0 underline">Retry</button>
        </p>
      )}
      {lines === null ? (
        loadError ? null : <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : lines.length === 0 ? (
        !loadError && <p className={`text-xs ${theme.textMuted}`}>No phone lines yet. New line makes the first one.</p>
      ) : lines.map((l) => (
        <div key={l.id} data-line={l.id} className={`flex items-center justify-between gap-2 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary} text-xs`}>
          <span className="flex flex-col gap-0.5 min-w-0">
            <span className="flex items-center gap-2">
              <span className="font-bold truncate">{l.name}</span>
              {closedByGate(l)
                ? <span className="text-amber-500">Closed</span>
                : <span className={l.enabled ? 'text-green-600' : theme.textMuted}>{l.enabled ? 'On' : 'Off'}</span>}
            </span>
            <span className={`text-[10px] ${theme.textMuted}`}>
              {`${l.profile?.name ?? 'Default profile'} · ${l.project?.name ?? 'No project yet'}`}
            </span>
            <span className={`text-[10px] ${theme.textMuted}`}>
              {plural(l.callsToday, 'call')} today{l.lastCallAt ? ` · last ${formatMoment(l.lastCallAt, zone)}` : ''}
            </span>
          </span>
          <button type="button" onClick={() => onEdit(l)} disabled={opening} aria-label={`Edit ${l.name}`} className="min-h-11 md:min-h-0 underline shrink-0">
            Edit
          </button>
        </div>
      ))}
    </>
  );
}

function Field({ theme, label, children }) {
  return (
    <div className="flex flex-col gap-1">
      <span className={`text-xs font-bold ${theme.text}`}>{label}</span>
      {children}
    </div>
  );
}

function LineEditor({ theme, api, saved, form, set, setName, setEngine, voices, profiles, projects, models, status,
  busy, dirty, error, changed, callsKey, zone, showToast, onSave, onCancel, onDelete, onRestore, onReload, onCallDeleted, projectsLoaded, engineNames }) {
  const input = `min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`;
  const engine = voices.find((v) => v.engine === form.engine);

  // The choices. The line's own saved value is always one of them, built from `saved` (not from the form, so
  // picking another value and then the saved one back still works), even when the server no longer offers it.
  const withSaved = (options, value, label) => (value !== undefined && value !== null && value !== ''
    && !options.some((o) => o.value === value) ? [...options, { value, label }] : options);
  const profileOptions = withSaved([{ value: '', label: 'Default profile' }, ...profiles
    .filter((p) => p.enabled || p.id === saved?.profile?.id).map((p) => ({ value: p.id, label: p.name }))],
  saved?.profile?.id, `${saved?.profile?.name ?? 'This profile'} (not offered now)`);
  const projectOptions = withSaved([{ value: '', label: 'No project yet' }, ...projects.map((p) => ({ value: p.id, label: p.name }))],
    saved?.project?.id, `${saved?.project?.name ?? 'This project'} (not offered now)`);
  const modelOptions = withSaved(models.map((m) => ({ value: m.id, label: modelLabel(m) })), saved?.model, `${saved?.model} (not offered now)`);
  const engineOptions = withSaved(voices.map((v) => ({ value: v.engine, label: v.label })), saved?.voice.engine,
    `${engineNames[saved?.voice.engine] ?? saved?.voice.engine} (not set up here)`);
  const onSavedEngine = saved && form.engine === saved.voice.engine;
  const voiceOptions = withSaved((engine?.voices ?? []).map((v) => ({ value: v, label: v })),
    onSavedEngine ? saved.voice.voice : undefined, `${saved?.voice.voice} (not offered now)`);
  const accentOptions = withSaved((engine?.accents ?? []).map((a) => ({ value: a.code, label: a.label })),
    onSavedEngine ? saved.voice.accent : undefined, `${saved?.voice.accent} (not offered now)`);
  const maintained = saved?.project ? projects.some((p) => p.id === saved.project.id) : false;
  const invalid = problemOf(form);

  const welcomeSaved = saved !== null && form.greeting.trim() === saved.greeting
    && sameVoice(voiceOf(form), { ...saved.voice, accent: saved.voice.accent ?? undefined });
  const canSave = !busy && form.name.trim() !== '' && form.greeting.trim() !== '' && form.model !== '' && (saved ? dirty : true) && invalid === null;

  return (
    <div className="flex flex-col gap-3">
      <h3 className={`text-xs font-bold ${theme.text}`}>{saved ? 'Edit line' : 'New line'}</h3>
      {error && (
        <p role="alert" className="flex flex-wrap items-center gap-2 text-xs text-red-500">
          {error}
          {changed && <button type="button" onClick={onReload} disabled={busy} className="min-h-11 md:min-h-0 underline">Load it again</button>}
        </p>
      )}

      <Field theme={theme} label="Name">
        <input aria-label="Name" value={form.name} maxLength={60} onChange={(e) => setName(e.target.value)} className={input} />
      </Field>
      <Field theme={theme} label="Answers as">
        <SearchableSelect theme={theme} aria-label="Answers as" findLabel="Find a profile" value={form.profileId}
          onChange={(profileId) => set({ profileId })} options={profileOptions} className={input} />
      </Field>
      <Field theme={theme} label="Knows">
        <SearchableSelect theme={theme} aria-label="Knows" findLabel="Find a project" value={form.projectId}
          onChange={(projectId) => set({ projectId, ...(projectId ? {} : { enabled: false }) })}
          options={projectOptions} className={input} />
        <p className={`text-[10px] ${theme.textSecondary}`}>Only projects you maintain or own are listed.</p>
        {form.projectId && <p className="text-[10px] text-amber-500">{WARNING}</p>}
        {saved?.access === false && (
          <p className="flex flex-wrap items-center gap-2 text-[10px] text-amber-500">
            No access to {saved.project?.name}
            {maintained || !projectsLoaded ? (
              <button type="button" onClick={onRestore} disabled={busy} className="min-h-11 md:min-h-0 underline">
                Give it access again
              </button>
            ) : (
              <span>Only a maintainer or owner of {saved.project?.name} can give it access again.</span>
            )}
          </p>
        )}
      </Field>
      <Field theme={theme} label="Model">
        <SearchableSelect theme={theme} aria-label="Model" findLabel="Find a model" value={form.model}
          onChange={(model) => set({ model })} options={modelOptions} className={input} />
      </Field>
      <Field theme={theme} label="Voice">
        <div className="flex flex-wrap items-center gap-2">
          <SearchableSelect theme={theme} aria-label="Voice engine" findLabel="Find an engine" value={form.engine}
            onChange={setEngine} options={engineOptions} className={input} wrapperClassName="!w-auto" />
          <SearchableSelect theme={theme} aria-label="Voice" findLabel="Find a voice" value={form.voice}
            onChange={(voice) => set({ voice })} options={voiceOptions} className={input} wrapperClassName="!w-auto" />
          {accentOptions.length > 0 && (
            <SearchableSelect theme={theme} aria-label="English accent" findLabel="Find an accent" value={form.accent}
              onChange={(accent) => set({ accent })} options={accentOptions}
              className={input} wrapperClassName="!w-auto" />
          )}
          <HearWelcome api={api} lineId={saved?.id} ready={welcomeSaved} onError={showToast} theme={theme} />
        </div>
      </Field>
      <Field theme={theme} label="Welcome message">
        <textarea aria-label="Welcome message" rows={3} maxLength={400} value={form.greeting}
          onChange={(e) => set({ greeting: e.target.value, greetingTouched: true })} className={input} />
      </Field>
      <div className="flex flex-wrap gap-4">
        <Field theme={theme} label="Longest call (minutes)">
          <input type="number" aria-label="Longest call (minutes)" min={1} max={60} step={1} value={form.maxCallMin}
            onChange={(e) => set({ maxCallMin: e.target.value })} className={`${input} w-24`} />
        </Field>
        <Field theme={theme} label="Calls at once">
          <input type="number" aria-label="Calls at once" min={1} max={20} step={1} value={form.maxCalls}
            onChange={(e) => set({ maxCalls: e.target.value })} className={`${input} w-24`} />
        </Field>
        <Field theme={theme} label="Tokens a day">
          <input type="number" aria-label="Tokens a day" min={0} step={1000} value={form.dailyTokens} placeholder="default"
            onChange={(e) => set({ dailyTokens: e.target.value })} className={`${input} w-32`} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-xs min-h-11 md:min-h-0">
        <input type="checkbox" aria-label="Line is on" checked={form.enabled} disabled={!form.projectId && !form.enabled}
          onChange={(e) => set({ enabled: e.target.checked })} />
        Line is on
        {!form.projectId && !form.enabled && <span className={`text-[10px] ${theme.textSecondary}`}>Pick a project to switch it on.</span>}
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onSave} disabled={!canSave}
          className="min-h-11 md:min-h-0 px-3 py-1 text-xs rounded border border-blue-500 text-blue-500 disabled:opacity-40">
          {busy ? <Loader2 size={12} className="animate-spin" aria-label="Saving" /> : 'Save line'}
        </button>
        {invalid && <span className="text-[10px] text-amber-500">{invalid}</span>}
        <button type="button" onClick={onCancel} disabled={busy} className="min-h-11 md:min-h-0 underline text-xs">
          {saved ? 'Back to the lines' : 'Cancel'}
        </button>
        {saved && (
          <button type="button" onClick={onDelete} disabled={busy} className="min-h-11 md:min-h-0 underline text-xs text-red-500">
            Delete line
          </button>
        )}
      </div>

      {saved && <Snippet theme={theme} line={saved} status={status} showToast={showToast} />}
      {saved && <Calls theme={theme} api={api} lineId={saved.id} zone={zone} showToast={showToast} reloadKey={callsKey} onDeleted={onCallDeleted} />}
    </div>
  );
}

/**
 * "Hear the welcome": plays the saved welcome from the server (the clip as callers hear it). Only for a saved line
 * whose welcome and voice are unchanged in the form, since the clip is what was saved (R12.5).
 */
function HearWelcome({ api, lineId, ready, onError, theme }) {
  const [loading, setLoading] = useState(false);
  const playing = useRef(null);   // { audio, url } while a clip's object URL is alive

  const release = useCallback(() => {
    if (!playing.current) return;
    playing.current.audio.pause?.();
    URL.revokeObjectURL(playing.current.url);
    playing.current = null;
  }, []);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; release(); };
  }, [release]);

  const hear = async () => {
    release();
    setLoading(true);
    try {
      const clip = await api.greeting(lineId);
      if (!mounted.current) return;   // the editor closed while the clip came: nothing is played, no URL is made
      const url = URL.createObjectURL(clip);
      const audio = new Audio(url);
      const mine = { audio, url };
      const done = () => { if (playing.current === mine) release(); };
      audio.onended = done;
      audio.onerror = done;
      playing.current = mine;
      await audio.play();
    } catch (e) {
      release();
      if (mounted.current) onError?.(e?.message || "The welcome couldn't be played.", 5000);
    } finally {
      if (mounted.current) setLoading(false);
    }
  };

  return (
    <span className="flex items-center gap-2">
      <button type="button" onClick={hear} disabled={!ready || loading}
        className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} disabled:opacity-40`}>
        Hear the welcome
      </button>
      {!ready && <span className={`text-[10px] ${theme.textSecondary}`}>Save to hear the new welcome.</span>}
    </span>
  );
}

/** The dialplan lines for the phone system, or what has to be set first. */
function Snippet({ theme, line, status, showToast }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(line.snippet);
      showToast?.('Copied the dialplan lines.', 3000);
    } catch {
      showToast?.("Couldn't copy: select the lines and copy them by hand.", 5000);
    }
  };
  // The line's snippet is null while the listener is off, or when it is on with no address a phone system can dial.
  const missing = status?.listening === false ? LISTENER_OFF : NO_PBX_ADDRESS;
  return (
    <div className="flex flex-col gap-1">
      <span className={`text-xs font-bold ${theme.text}`}>Connect your phone system (Asterisk)</span>
      {line.snippet ? (
        <>
          <pre data-testid="phone-snippet" className={`p-2 text-[11px] rounded border ${theme.border} ${theme.bgSecondary} overflow-x-auto`}>
            {line.snippet}
          </pre>
          <div className="flex items-center gap-3">
            <span className={`text-[10px] ${theme.textSecondary}`}>{SNIPPET_HINT}</span>
            <button type="button" onClick={copy} className="min-h-11 md:min-h-0 underline text-xs">Copy</button>
          </div>
        </>
      ) : (
        <p className={`text-[10px] ${theme.textSecondary}`}>{status ? missing : 'The listener could not be read.'}</p>
      )}
    </div>
  );
}

/** The line's calls, newest first, each with its transcript to open and a way to delete it. */
function Calls({ theme, api, lineId, zone, showToast, reloadKey, onDeleted }) {
  const [calls, setCalls] = useState(null);
  const [failed, setFailed] = useState(null);
  const [open, setOpen] = useState(null);   // { id, messages } of the transcript on show
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      setCalls(await api.calls(lineId));
      setFailed(null);
    } catch (e) {
      setFailed(e.message);
    }
  }, [api, lineId]);
  useEffect(() => { load(); }, [load, reloadKey]);

  const toggle = async (c) => {
    if (open?.id === c.id) { setOpen(null); return; }
    setBusyId(c.id);
    try {
      setOpen({ id: c.id, messages: (await api.call(c.id)).messages });
    } catch (e) {
      showToast?.(e.message, 5000);
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (c) => {
    if (!window.confirm(c.transcript ? 'Delete this call and its transcript?' : 'Delete this call?')) return;
    setBusyId(c.id);
    try {
      const { transcripts } = await api.deleteCall(c.id);
      setCalls((all) => all.filter((x) => x.id !== c.id));
      if (open?.id === c.id) setOpen(null);
      onDeleted?.(transcripts ?? 0);
      load();
    } catch (e) {
      showToast?.(e.message, 5000);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3">
        <span className={`text-xs font-bold ${theme.text}`}>Calls</span>
        <button type="button" onClick={load} className="min-h-11 md:min-h-0 underline text-[10px]">Refresh</button>
      </div>
      {failed && <p role="alert" className="text-xs text-red-500">Could not load the calls: {failed}</p>}
      {calls?.length === 0 && <p className={`text-[10px] ${theme.textMuted}`}>No calls yet.</p>}
      {calls?.map((c) => (
        <div key={c.id} data-call={c.id} className={`flex flex-col gap-1 p-2 rounded border ${theme.border} text-xs`}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>{formatMoment(c.startedAt, zone)}</span>
            {c.endedAt && <span>{lengthText(c.lengthS)}</span>}
            <span>{c.endedAt ? (END_REASONS[c.endReason] ?? c.endReason) : 'in progress'}</span>
            <span>{plural(c.turns, 'turn')}</span>
            {c.slowestFirstSoundS != null && <span>{`slowest ${c.slowestFirstSoundS} s`}</span>}
            {c.transcript && (
              <button type="button" onClick={() => toggle(c)} disabled={busyId === c.id} className="min-h-11 md:min-h-0 underline">
                Transcript
              </button>
            )}
            <button type="button" onClick={() => remove(c)} disabled={busyId === c.id} className="min-h-11 md:min-h-0 underline text-red-500">
              Delete call
            </button>
          </div>
          {open?.id === c.id && (
            <div data-testid="phone-transcript" className="flex flex-col gap-1 pl-2">
              {open.messages.map((m) => (
                <p key={m.id}>
                  <span className={`font-bold ${theme.textSecondary}`}>{m.role === 'user' ? 'Caller' : 'Line'}: </span>
                  <span>{m.content}</span>
                </p>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
