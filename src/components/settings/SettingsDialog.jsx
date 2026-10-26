import { useEffect, useRef, useState } from 'react';
import { KOKORO_VOICES } from '../../constants';
import { PlayCircle, Square, Clock, VolumeX, Volume1, Volume2, Moon, Sun, Zap, X } from 'lucide-react';
import { InferenceRow } from '../chat/InferenceRow';
import { KEEP_ALIVE_OPTIONS, NUM_CTX_OPTIONS, serverContextSize, shownValue, withAutoLabel, withinLimit } from '../../lib/inferenceLimits';
import { AccountPanel } from '../account/AccountPanel';
import { resolveForModel, patchForModel } from '../../hooks/inference';
import { groupByProvider, migrateModelId, modelLabel, supportedKnobs } from '../../lib/modelIds';

function Field({ theme, label, children }) {
    return (
        <div className="flex flex-col gap-1.5">
            <span className={`text-[10px] font-bold uppercase tracking-wider ${theme.textMuted}`}>{label}</span>
            {children}
        </div>
    );
}

// The tabs, in order. `profile` shows only when the dialog is given a
// profile panel (a signed-in user).
const SETTINGS_TABS = [
    { id: 'profile', label: 'Profile' },
    { id: 'voice', label: 'Voice & reading' },
    { id: 'chat', label: 'Chat' },
    { id: 'appearance', label: 'Appearance' },
    { id: 'connection', label: 'Connection' },
    { id: 'tokens', label: 'Access tokens' },
];

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Settings, as a dialog over whatever is open: a voice change or turning
 * thinking off no longer leaves the page, so the narration, a reply being
 * written and the reader's place all carry on underneath. It only holds the
 * CONTROLS: the state stays App-level (usePersistedState) and comes in
 * grouped prop bags.
 *
 * `tab` is the open tab (null: closed); `onTab` switches it and `onClose`
 * closes the dialog. Escape, the backdrop and the close button all close it,
 * and focus goes back to whatever opened it.
 */
export default function SettingsDialog({ theme, tab, onTab, onClose, profile = null, voiceSettings, chatSettings, connectionSettings, appearanceSettings, accountProps }) {
    const open = tab != null;
    const tabs = SETTINGS_TABS.filter((t) => t.id !== 'profile' || profile);
    const active = tabs.some((t) => t.id === tab) ? tab : tabs[0].id;
    const boxRef = useRef(null);

    // Focus moves into the dialog on open and back to the opener on close.
    useEffect(() => {
        if (!open) return undefined;
        const opener = document.activeElement;
        boxRef.current?.querySelector('[role="tab"][aria-selected="true"]')?.focus();
        return () => { if (opener && typeof opener.focus === 'function') opener.focus(); };
    }, [open]);

    if (!open) return null;

    const onKeyDown = (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); onClose(); return; }
        if (e.key !== 'Tab') return;
        // Keep Tab inside the dialog.
        const items = [...boxRef.current.querySelectorAll(FOCUSABLE)];
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };

    const onTabKey = (e) => {
        const i = tabs.findIndex((t) => t.id === active);
        const next = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        if (!next) return;
        e.preventDefault();
        const to = tabs[(i + next + tabs.length) % tabs.length];
        onTab(to.id);
        boxRef.current?.querySelector(`#settings-tab-${to.id}`)?.focus();
    };

    return (
        <div
            className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-sm flex items-stretch md:items-center justify-center md:p-6"
            onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                ref={boxRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="settings-title"
                // Focusable, so a click on blank space inside keeps focus (and
                // Escape) in the dialog instead of handing it to the page.
                tabIndex={-1}
                onKeyDown={onKeyDown}
                className={`outline-none w-full md:max-w-4xl h-full md:h-[min(85vh,760px)] flex flex-col md:flex-row overflow-hidden md:rounded-2xl shadow-2xl border ${theme.border} ${theme.bg} ${theme.text}`}
            >
                <div className={`shrink-0 md:w-52 flex flex-col border-b md:border-b-0 md:border-r ${theme.border} ${theme.bgSecondary}`}>
                    <div className="flex items-center justify-between px-4 py-3 md:py-4">
                        <h1 id="settings-title" className="text-sm font-bold">Settings</h1>
                        <button onClick={onClose} aria-label="Close settings" title="Close (Esc)"
                            className={`md:hidden p-1.5 rounded-lg ${theme.hover} ${theme.textSecondary}`}>
                            <X size={18} />
                        </button>
                    </div>
                    <div role="tablist" aria-label="Settings sections" aria-orientation="vertical" onKeyDown={onTabKey}
                        className="flex md:flex-col gap-1 px-2 pb-2 overflow-x-auto md:overflow-visible">
                        {tabs.map((t) => (
                            <button
                                key={t.id}
                                id={`settings-tab-${t.id}`}
                                role="tab"
                                aria-selected={t.id === active}
                                aria-controls="settings-panel"
                                tabIndex={t.id === active ? 0 : -1}
                                onClick={() => onTab(t.id)}
                                className={`shrink-0 text-left text-xs font-bold px-3 py-2 rounded-lg transition-colors whitespace-nowrap ${t.id === active ? 'bg-blue-600 text-white shadow' : `${theme.textSecondary} ${theme.hover}`}`}
                            >
                                {t.label}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="flex-1 min-h-0 flex flex-col">
                    <div className={`hidden md:flex items-center justify-between px-6 py-4 border-b ${theme.border}`}>
                        <h2 className="text-sm font-bold">{tabs.find((t) => t.id === active).label}</h2>
                        <button onClick={onClose} aria-label="Close settings" title="Close (Esc)"
                            className={`p-1.5 rounded-lg ${theme.hover} ${theme.textSecondary}`}>
                            <X size={18} />
                        </button>
                    </div>
                    <div id="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${active}`}
                        className="flex-1 overflow-y-auto px-4 md:px-6 py-5">
                        <div className="flex flex-col gap-5 max-w-xl">
                            {active === 'profile' && profile}
                            {active === 'voice' && <VoicePanel theme={theme} v={voiceSettings} />}
                            {active === 'chat' && <ChatPanel theme={theme} ch={chatSettings} />}
                            {active === 'appearance' && <AppearancePanel theme={theme} a={appearanceSettings} />}
                            {active === 'connection' && <ConnectionPanel theme={theme} c={connectionSettings} />}
                            {active === 'tokens' && (
                                <AccountPanel theme={theme} apiHost={accountProps.apiHost} apiPort={accountProps.apiPort} user={accountProps.user} />
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

const inputClass = (theme) => `text-xs font-bold p-2 rounded-lg border ${theme.border} ${theme.bgTertiary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors`;

function VoicePanel({ theme, v }) {
    const currentVoice = KOKORO_VOICES.find((x) => x.id === v.selectedVoice);
    const VolumeIcon = v.volume === 0 ? VolumeX : v.volume < 0.5 ? Volume1 : Volume2;
    const input = inputClass(theme);
    return (
        <>
                <Field theme={theme} label="Voice">
                    <div className="flex gap-2">
                        <select
                            aria-label="Voice"
                            value={v.selectedVoice}
                            onChange={(e) => { v.setSelectedVoice(e.target.value); v.clearCache(); }}
                            className={`flex-1 ${input}`}
                        >
                            {KOKORO_VOICES.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                        </select>
                        <button
                            onClick={() => (v.isPreviewingVoice ? v.stopVoicePreview() : v.previewVoice(v.selectedVoice))}
                            title={v.isPreviewingVoice ? 'Stop preview' : 'Preview this voice'}
                            className={`px-3 rounded-lg border transition-all flex items-center justify-center ${v.isPreviewingVoice ? 'bg-blue-600 text-white border-blue-600 animate-pulse' : `${theme.border} ${theme.hover} ${theme.textSecondary}`}`}
                        >
                            {v.isPreviewingVoice ? <Square size={14} /> : <PlayCircle size={14} />}
                        </button>
                    </div>
                    {currentVoice?.sampleText && (
                        <p className={`text-[10px] leading-relaxed ${theme.textMuted} italic mt-1`}>"{currentVoice.sampleText}"</p>
                    )}
                </Field>

                <Field theme={theme} label="Speed">
                    <select
                        aria-label="Speed"
                        value={v.playbackSpeed}
                        onChange={(e) => { v.setPlaybackSpeed(parseFloat(e.target.value)); v.clearCache(); }}
                        className={`w-full ${input}`}
                    >
                        {[0.5, 0.75, 1.0, 1.25, 1.5, 2.0].map((s) => <option key={s} value={s}>{s}x Speed</option>)}
                    </select>
                </Field>

                <Field theme={theme} label="Volume">
                    <div className={`flex items-center gap-3 p-2 rounded-lg border ${theme.border} ${theme.bgTertiary}`}>
                        <VolumeIcon size={16} className={theme.textSecondary} />
                        <input
                            type="range" min="0" max="1" step="0.1" aria-label="Volume"
                            value={v.volume}
                            onChange={(e) => v.setVolume(parseFloat(e.target.value))}
                            className="flex-1 h-2 appearance-none bg-slate-300 dark:bg-slate-600 rounded-full cursor-pointer accent-blue-500"
                        />
                        <span className={`text-xs font-bold ${theme.textSecondary} w-8`}>{Math.round(v.volume * 100)}%</span>
                    </div>
                </Field>

                <Field theme={theme} label="Voice backend">
                    <button
                        onClick={() => v.setIsLocalhost((x) => !x)}
                        aria-label="Voice backend"
                        className={`w-full flex items-center justify-between p-2 rounded-lg border ${theme.border} ${theme.bgTertiary} ${theme.hover} text-xs font-bold transition-colors`}
                    >
                        <span className="flex items-center gap-2"><Zap size={13} fill={v.isLocalhost ? 'currentColor' : 'none'} className={v.isLocalhost ? 'text-blue-500' : theme.textMuted} /> {v.isLocalhost ? 'Kokoro (server)' : 'System voices'}</span>
                        <span className={theme.textMuted}>tap to switch</span>
                    </button>
                </Field>

                <Field theme={theme} label="Request timeout">
                    <div className={`flex items-center gap-2 p-2 rounded-lg border ${theme.border} ${theme.bgTertiary}`}>
                        <Clock size={14} className={theme.textSecondary} />
                        <input
                            type="number" min="5" max="120" aria-label="Request timeout"
                            value={v.requestTimeout}
                            onChange={(e) => v.setRequestTimeout(Math.max(5, Math.min(120, parseInt(e.target.value) || 15)))}
                            className={`w-16 text-xs font-bold text-center ${theme.bgSecondary} ${theme.text} rounded-lg p-1.5 outline-none focus:ring-2 focus:ring-blue-500`}
                        />
                        <span className={`text-[10px] font-bold ${theme.textMuted}`}>seconds</span>
                    </div>
                    <label className="flex items-center gap-2 mt-1.5 cursor-pointer">
                        <input type="checkbox" checked={v.unlimitedBatchTimeout} onChange={(e) => v.setUnlimitedBatchTimeout(e.target.checked)} className="accent-blue-500 w-3.5 h-3.5" />
                        <span className={`text-[10px] font-bold ${theme.textMuted}`}>Unlimited batch/download timeout</span>
                    </label>
                </Field>
        </>
    );
}

function ChatPanel({ theme, ch }) {
    // Which model's per-model inference knobs are being edited (defaults to the
    // active chat model). Editing writes inferenceByModel[configModel] via the
    // same pure helpers the chat sidebar used.
    const [pickedModel, setConfigModel] = useState(ch.selectedModel || ch.availableModels[0]?.id || '');
    // Opened before the model list arrived, the pick can still be a pre-C1 bare
    // name ("qwen2.5:7b"); edit its `provider:name` id instead, never the bare key.
    const configModel = migrateModelId(pickedModel, ch.availableModels);
    const inf = resolveForModel(ch.inferenceByModel, configModel, ch.inferenceBase);
    const setInf = (patch) => ch.setInferenceByModel((prev) => patchForModel(prev, configModel, patch));
    const configEntry = ch.availableModels.find((m) => m.id === configModel);
    const knobs = supportedKnobs(configEntry);
    // v2.4 Task F: only the context sizes and keep-alive times the server will honour.
    // Admin console v2: a self-hosted Ollama model also says the size the server will load it with.
    // A fixed size locks the row and shows the admin's number, the same one the help line names (ruling P13). The
    // person's own value stays stored, untouched: it applies again if the admin unsets the size.
    const serverCtx = serverContextSize(configEntry);
    const ctxFixed = serverCtx?.fixedNumCtx ?? null;
    const ctxOptions = ctxFixed !== null
        ? [[String(ctxFixed), ctxFixed.toLocaleString('en-US')]]
        : withAutoLabel(withinLimit(NUM_CTX_OPTIONS, configEntry?.limits?.numCtxMax, 'numCtx'), serverCtx?.autoNumCtx);
    const keepOptions = withinLimit(KEEP_ALIVE_OPTIONS, configEntry?.limits?.keepAliveMaxS, 'keepAlive');
    const input = inputClass(theme);
    return (
        <>
                <Field theme={theme} label="Read-aloud mode">
                    <div className={`flex p-1 rounded-lg border ${theme.border} ${theme.bgTertiary}`}>
                        <button onClick={() => ch.setChatTtsMode('streaming')} className={`flex-1 text-[10px] font-bold py-1.5 rounded-md transition-colors ${ch.chatTtsMode === 'streaming' ? 'bg-blue-600 text-white shadow' : theme.textSecondary}`}>Streaming</button>
                        <button onClick={() => ch.setChatTtsMode('after-complete')} className={`flex-1 text-[10px] font-bold py-1.5 rounded-md transition-colors ${ch.chatTtsMode === 'after-complete' ? 'bg-blue-600 text-white shadow' : theme.textSecondary}`}>After complete</button>
                    </div>
                    <button onClick={() => ch.setChatAutoTts(!ch.chatAutoTts)} className={`w-full flex items-center justify-between p-2 mt-1 rounded-lg border ${theme.border} ${theme.bgTertiary} ${theme.hover} text-xs font-bold transition-colors`}>
                        <span className={theme.textSecondary}>Auto read-aloud</span>
                        {ch.chatAutoTts ? <Volume2 size={14} className="text-blue-500" /> : <VolumeX size={14} className={theme.textMuted} />}
                    </button>
                </Field>

                <Field theme={theme} label="Per-model inference">
                    <select
                        aria-label="Configuring model"
                        value={configModel}
                        onChange={(e) => setConfigModel(e.target.value)}
                        className={`w-full ${input}`}
                    >
                        {ch.availableModels.length === 0
                            ? <option value="">No models available</option>
                            : <>
                                {configModel && !ch.availableModels.some((m) => m.id === configModel) && (
                                    <option value={configModel} disabled>{configModel} (unavailable)</option>
                                )}
                                {groupByProvider(ch.availableModels).map(([provider, models]) => (
                                    <optgroup key={provider} label={provider}>
                                        {models.map((m) => <option key={m.id} value={m.id}>{modelLabel(m)}</option>)}
                                    </optgroup>
                                ))}
                            </>}
                    </select>
                    <div className="flex flex-col gap-2 mt-1">
                        {knobs.numCtx && <InferenceRow theme={theme} label="Context window" disabled={!configModel || ctxFixed !== null}
                            help={ctxFixed !== null ? `Set by your admin: ${ctxFixed.toLocaleString('en-US')} tokens.` : null}
                            value={ctxFixed !== null ? String(ctxFixed)
                                : shownValue(inf.numCtx === null ? 'auto' : String(inf.numCtx), ctxOptions, 'numCtx')}
                            onChange={(val) => setInf({ numCtx: val === 'auto' ? null : Number(val) })}
                            options={ctxOptions} />}
                        {knobs.keepAlive && <InferenceRow theme={theme} label="Keep model warm" disabled={!configModel}
                            value={shownValue(inf.keepAlive === null ? 'auto' : String(inf.keepAlive), keepOptions, 'keepAlive')}
                            onChange={(val) => setInf({ keepAlive: val === 'auto' ? null : (val === '-1' ? -1 : val) })}
                            options={keepOptions} />}
                        {knobs.think && <InferenceRow theme={theme} label="Thinking" disabled={!configModel}
                            value={inf.think}
                            onChange={(val) => setInf({ think: val })}
                            options={[['off', 'Off'], ['on', 'On'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High']]} />}
                        <InferenceRow theme={theme} label="Max reply tokens" disabled={!configModel}
                            value={inf.numPredict === null ? 'auto' : String(inf.numPredict)}
                            onChange={(val) => setInf({ numPredict: val === 'auto' ? null : Number(val) })}
                            options={[['auto', 'Unlimited'], ['512', '512'], ['1024', '1024'], ['2048', '2048'], ['4096', '4096']]} />
                    </div>
                    <p className={`text-[9px] ${theme.textMuted} mt-1`}>Saved per model. Only the settings this model's provider supports are shown; changing an Ollama model's context window reloads it.</p>
                </Field>
        </>
    );
}

function ConnectionPanel({ theme, c }) {
    const input = inputClass(theme);
    return (
        <>
                <Field theme={theme} label="Voice / API host">
                    <div className="flex gap-2">
                        <input type="text" aria-label="API host" placeholder="blank = same origin" value={c.apiHost} onChange={(e) => c.setApiHost(e.target.value)} className={`flex-1 ${input}`} />
                        <input type="text" aria-label="API port" placeholder="8000" value={c.apiPort} onChange={(e) => c.setApiPort(e.target.value)} className={`w-24 ${input}`} />
                    </div>
                    {!c.apiHost?.trim() && (
                        <p className={`text-[9px] ${theme.textMuted}`}>Same-origin mode — requests go to <code>/v1/*</code> on the page's host.</p>
                    )}
                    <span className={`text-[10px] ${c.backendAvailable === null || c.backendAvailable === undefined ? theme.textMuted : c.backendAvailable ? 'text-green-500' : 'text-red-400'}`}>
                        {c.backendAvailable === null || c.backendAvailable === undefined ? '⏳ Checking…' : c.backendAvailable ? '✓ Connected' : '✗ Unavailable'}
                    </span>
                </Field>
        </>
    );
}

function AppearancePanel({ theme, a }) {
    const input = inputClass(theme);
    return (
        <>
                <button
                    onClick={() => a.setDarkMode((x) => !x)}
                    aria-label="Dark mode"
                    className={`w-full flex items-center justify-between p-2 rounded-lg border ${theme.border} ${theme.bgTertiary} ${theme.hover} text-xs font-bold transition-colors`}
                >
                    <span className="flex items-center gap-2">{a.darkMode ? <Sun size={14} /> : <Moon size={14} />} {a.darkMode ? 'Light mode' : 'Dark mode'}</span>
                </button>

                <Field theme={theme} label="Layout mode">
                    <select aria-label="Layout mode" value={a.layoutMode} onChange={(e) => a.setLayoutMode(e.target.value)} className={`w-full ${input}`}>
                        <option value="auto">Auto (detect screen)</option>
                        <option value="desktop">Force Desktop</option>
                        <option value="mobile">Force Mobile</option>
                    </select>
                    {a.layoutMode === 'auto' && (
                        <div className="flex items-center gap-2 mt-1">
                            <span className={`text-[10px] font-bold ${theme.textMuted}`}>Mobile below</span>
                            <input type="number" min="320" max="1440" aria-label="Mobile breakpoint" value={a.mobileBreakpoint} onChange={(e) => a.setMobileBreakpoint(parseInt(e.target.value) || 768)} className={`w-20 ${input}`} />
                            <span className={`text-[10px] ${theme.textMuted}`}>px</span>
                        </div>
                    )}
                    <label className="flex items-center gap-2 mt-1.5 cursor-pointer">
                        <input type="checkbox" checked={a.showHeaderControlsOnMobile} onChange={(e) => a.setShowHeaderControlsOnMobile(e.target.checked)} className="accent-blue-500 w-3.5 h-3.5" />
                        <span className={`text-[10px] font-bold ${theme.textMuted}`}>Show header controls on mobile</span>
                    </label>
                </Field>
        </>
    );
}
