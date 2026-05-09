import { useEffect, useRef, useState } from 'react';
import { Mic, Square, X, Check, Loader2 } from 'lucide-react';
import { fileToAttachment } from '../utils/attachment';

const PREFERRED_MIME = 'audio/webm;codecs=opus';

const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
};

/**
 * MediaRecorder-based voice clip capture.
 *
 * Flow: idle → recording (mic permission + capturing) → preview (audio playback +
 * Attach / Discard). On Attach, calls onAttach(attachment); on Discard or Cancel,
 * calls onCancel(). The parent owns whether this panel is mounted.
 */
export default function VoiceRecorder({ theme, darkMode, onAttach, onCancel, showToast }) {
    const [phase, setPhase] = useState('idle'); // 'idle' | 'recording' | 'preview' | 'error'
    const [elapsed, setElapsed] = useState(0);
    const [previewUrl, setPreviewUrl] = useState(null);
    const [errorMsg, setErrorMsg] = useState('');
    const [busy, setBusy] = useState(false);

    const mediaRecorderRef = useRef(null);
    const streamRef = useRef(null);
    const chunksRef = useRef([]);
    const tickRef = useRef(null);
    const blobRef = useRef(null);

    // Cleanup on unmount: stop tracks, kill timer, revoke preview URL.
    useEffect(() => () => {
        if (tickRef.current) clearInterval(tickRef.current);
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
            try { mediaRecorderRef.current.stop(); } catch { /* noop */ }
        }
        if (streamRef.current) {
            streamRef.current.getTracks().forEach(t => t.stop());
            streamRef.current = null;
        }
        if (previewUrl) URL.revokeObjectURL(previewUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const stopStream = () => {
        if (streamRef.current) {
            streamRef.current.getTracks().forEach(t => t.stop());
            streamRef.current = null;
        }
    };

    const startRecording = async () => {
        if (typeof MediaRecorder === 'undefined') {
            setErrorMsg('MediaRecorder API not available in this browser.');
            setPhase('error');
            return;
        }
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            streamRef.current = stream;
            const mimeType = MediaRecorder.isTypeSupported(PREFERRED_MIME) ? PREFERRED_MIME : '';
            const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
            mediaRecorderRef.current = recorder;
            chunksRef.current = [];
            recorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
            };
            recorder.onstop = () => {
                const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
                blobRef.current = blob;
                const url = URL.createObjectURL(blob);
                setPreviewUrl(url);
                setPhase('preview');
                stopStream();
            };
            recorder.start();
            setElapsed(0);
            setPhase('recording');
            tickRef.current = setInterval(() => setElapsed(e => e + 1), 1000);
        } catch (e) {
            console.warn('Mic permission denied or failed:', e);
            setErrorMsg(e.name === 'NotAllowedError' ? 'Microphone permission denied.' : 'Could not access microphone.');
            setPhase('error');
        }
    };

    const stopRecording = () => {
        if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
            mediaRecorderRef.current.stop();
        }
    };

    const discard = () => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        setPreviewUrl(null);
        blobRef.current = null;
        setElapsed(0);
        setPhase('idle');
        onCancel?.();
    };

    const attach = async () => {
        if (!blobRef.current) return;
        setBusy(true);
        const ext = (blobRef.current.type.split('/')[1] || 'webm').split(';')[0];
        const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        const fakeFile = new File([blobRef.current], `voice-memo-${stamp}.${ext}`, { type: blobRef.current.type });
        const result = await fileToAttachment(fakeFile, { fallbackName: 'voice memo' });
        setBusy(false);
        if (!result.ok) {
            showToast?.(result.error, 4000);
            discard();
            return;
        }
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        setPreviewUrl(null);
        blobRef.current = null;
        setElapsed(0);
        setPhase('idle');
        onAttach?.(result.attachment);
    };

    return (
        <div className={`p-3 rounded-xl border ${theme.border} ${darkMode ? 'bg-slate-800/60' : 'bg-slate-100'} flex items-center gap-3`}>
            {phase === 'idle' && (
                <>
                    <button
                        onClick={startRecording}
                        className="p-2 rounded-full bg-red-500 hover:bg-red-600 text-white shadow"
                        title="Start recording"
                    >
                        <Mic size={16} />
                    </button>
                    <span className={`text-xs ${theme.textSecondary}`}>Tap to record</span>
                    <button
                        onClick={onCancel}
                        className={`ml-auto p-1.5 rounded-md ${theme.hover} ${theme.textMuted} hover:text-red-500`}
                        title="Close recorder"
                    >
                        <X size={14} />
                    </button>
                </>
            )}

            {phase === 'recording' && (
                <>
                    <span className="relative flex h-3 w-3">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500"></span>
                    </span>
                    <span className={`text-xs font-bold ${theme.text}`}>Recording…</span>
                    <span className={`text-xs font-mono ${theme.textMuted}`}>{formatTime(elapsed)}</span>
                    <button
                        onClick={stopRecording}
                        className="ml-auto p-2 rounded-full bg-slate-700 hover:bg-slate-800 text-white"
                        title="Stop"
                    >
                        <Square size={14} fill="currentColor" />
                    </button>
                </>
            )}

            {phase === 'preview' && previewUrl && (
                <>
                    <audio controls src={previewUrl} className="flex-1 min-w-0 h-9" />
                    <span className={`text-[10px] font-mono ${theme.textMuted} shrink-0`}>{formatTime(elapsed)}</span>
                    <button
                        onClick={discard}
                        className={`p-1.5 rounded-md ${theme.hover} ${theme.textMuted} hover:text-red-500`}
                        title="Discard"
                    >
                        <X size={14} />
                    </button>
                    <button
                        onClick={attach}
                        disabled={busy}
                        className="p-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white shadow disabled:opacity-60"
                        title="Attach to message"
                    >
                        {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                    </button>
                </>
            )}

            {phase === 'error' && (
                <>
                    <span className={`text-xs text-red-400`}>{errorMsg}</span>
                    <button
                        onClick={onCancel}
                        className={`ml-auto p-1.5 rounded-md ${theme.hover} ${theme.textMuted} hover:text-red-500`}
                        title="Close"
                    >
                        <X size={14} />
                    </button>
                </>
            )}
        </div>
    );
}
