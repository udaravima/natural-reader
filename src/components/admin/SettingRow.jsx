/**
 * Admin console: the row every setting shares (admin console v2 spec §3). A label,
 * its help, the control (children), where the value comes from, and a way back to
 * the server's own value, so no screen leaves a setting the admin can't undo.
 * SectionSaveBar is the one Save a section has; rows only draft (useSectionSettings).
 */

const SOURCE_LINE = {
  admin: () => 'Set here.',
  env: (envName) => <>From the server setting <code>{envName}</code>.</>,
  default: () => 'The default.',
};

// Also the source line of a read-only value (the Deployment list), so the wording lives here once.
export function SourceLine({ source, envName }) {
  return <>{(SOURCE_LINE[source] ?? (() => source))(envName)}</>;
}

// `group`: the row's control is several (radios, a set of checkboxes), so there is no one control to label.
// The label becomes the group's name instead: the caller wraps its controls in an element with
// aria-labelledby={`${id}-label`}.
export function SettingRow({ theme, id, label, help, source, envName, canUseServer, onUseServer, group, children }) {
  const labelClass = `text-xs font-bold ${theme.text}`;
  return (
    <div className="flex flex-col gap-1">
      {group
        ? <span id={`${id}-label`} className={labelClass}>{label}</span>
        : <label htmlFor={id} className={labelClass}>{label}</label>}
      {help && <p id={`${id}-help`} className={`text-[10px] ${theme.textSecondary}`}>{help}</p>}
      {children}
      <div className="flex flex-wrap items-center gap-3 text-[10px]">
        <span id={`${id}-source`} className={theme.textSecondary}>
          <SourceLine source={source} envName={envName} />
        </span>
        {canUseServer && (
          <button
            type="button"
            onClick={onUseServer}
            className="min-h-11 md:min-h-0 underline text-blue-500"
          >
            Use the server setting
          </button>
        )}
      </div>
    </div>
  );
}

// `canSave` false: something on screen is invalid, so Save stays off until it is put right.
export function SectionSaveBar({ theme, dirty, saving, error, canSave = true, onSave, onDiscard }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <button
          type="button"
          onClick={onSave}
          disabled={!dirty || saving || !canSave}
          className="min-h-11 md:min-h-0 px-3 py-1 rounded bg-blue-500 text-white font-bold disabled:opacity-40"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={onDiscard}
            disabled={saving}
            className={`min-h-11 md:min-h-0 underline ${theme.textSecondary} disabled:opacity-40 disabled:no-underline`}
          >
            Discard changes
          </button>
        )}
      </div>
      {error && <p role="alert" className="text-xs text-red-500">{error}</p>}
    </div>
  );
}
