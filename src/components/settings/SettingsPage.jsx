import { KOKORO_VOICES } from '../../constants';
import { PlayCircle, Square, Clock, VolumeX, Volume1, Volume2, Moon, Sun, Zap } from 'lucide-react';

// A titled settings section. `id` anchors nothing yet but keeps headings stable.
function Section({ theme, title, children }) {
    return (
        <section className="flex flex-col gap-3">
            <h2 className={`text-sm font-black uppercase tracking-widest ${theme.textSecondary}`}>{title}</h2>
            <div className={`flex flex-col gap-4 p-4 rounded-xl border ${theme.border} ${theme.bgSecondary}`}>
                {children}
            </div>
        </section>
    );
}

function Field({ theme, label, children }) {
    return (
        <div className="flex flex-col gap-1.5">
            <span className={`text-[10px] font-bold uppercase tracking-wider ${theme.textMuted}`}>{label}</span>
            {children}
        </div>
    );
}

/**
 * The consolidated Settings view (viewMode:'settings'). Holds every app setting
 * that used to live in the reader/chat sidebars and the Header. It only moves
 * the CONTROLS — the state stays App-level (usePersistedState) and is handed in
 * via grouped prop bags — so there is no persistence or behaviour change.
 *
 * The Chat & Inference and Account sections are filled by later tasks; the
 * scaffold renders all five headings.
 */
export default function SettingsPage({ theme, voiceSettings, connectionSettings, appearanceSettings }) {
    const v = voiceSettings;
    const c = connectionSettings;
    const a = appearanceSettings;
    const currentVoice = KOKORO_VOICES.find((x) => x.id === v.selectedVoice);
    const VolumeIcon = v.volume === 0 ? VolumeX : v.volume < 0.5 ? Volume1 : Volume2;
    const input = `text-xs font-bold p-2 rounded-lg border ${theme.border} ${theme.bgTertiary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors`;

    return (
        <div className={`h-full w-full overflow-y-auto ${theme.bg} ${theme.text}`}>
            <div className="max-w-3xl mx-auto px-4 md:px-8 py-6 flex flex-col gap-8">
                <h1 className="text-lg font-bold">Settings</h1>

                {/* ---------- Voice & Reading ---------- */}
                <Section theme={theme} title="Voice & Reading">
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
                </Section>

                {/* ---------- Chat & Inference (filled in Task 5) ---------- */}
                <Section theme={theme} title="Chat & Inference">
                    <p className={`text-xs ${theme.textMuted}`}>Chat and inference settings.</p>
                </Section>

                {/* ---------- Connection ---------- */}
                <Section theme={theme} title="Connection">
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

                    {c.inferenceSource === 'local' && (
                        <Field theme={theme} label="Ollama host (local inference)">
                            <div className="flex gap-2">
                                <input type="text" aria-label="Ollama host" placeholder="blank = same origin" value={c.ollamaHost} onChange={(e) => c.setOllamaHost(e.target.value)} className={`flex-1 ${input}`} />
                                <input type="text" aria-label="Ollama port" placeholder="11434" value={c.ollamaPort} onChange={(e) => c.setOllamaPort(e.target.value)} className={`w-24 ${input}`} />
                            </div>
                        </Field>
                    )}
                </Section>

                {/* ---------- Appearance ---------- */}
                <Section theme={theme} title="Appearance">
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
                </Section>

                {/* ---------- Account (filled in Task 6) ---------- */}
                <Section theme={theme} title="Account">
                    <p className={`text-xs ${theme.textMuted}`}>Account and access tokens.</p>
                </Section>
            </div>
        </div>
    );
}
