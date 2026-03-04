import { useMemo } from 'react';

/**
 * Returns a theme class map based on dark mode state.
 */
export function useTheme(darkMode) {
    return useMemo(() => ({
        bg: darkMode ? 'bg-slate-900' : 'bg-slate-50',
        bgSecondary: darkMode ? 'bg-slate-800' : 'bg-white',
        bgTertiary: darkMode ? 'bg-slate-700' : 'bg-slate-100',
        text: darkMode ? 'text-slate-100' : 'text-slate-900',
        textSecondary: darkMode ? 'text-slate-400' : 'text-slate-500',
        textMuted: darkMode ? 'text-slate-500' : 'text-slate-400',
        border: darkMode ? 'border-slate-700' : 'border-slate-200',
        borderSecondary: darkMode ? 'border-slate-600' : 'border-slate-100',
        hover: darkMode ? 'hover:bg-slate-700' : 'hover:bg-white',
        selection: darkMode ? 'selection:bg-blue-900' : 'selection:bg-blue-200',
        canvasBg: darkMode ? 'bg-slate-800' : 'bg-white',
        viewportBg: darkMode ? 'bg-slate-950/50' : 'bg-slate-200/50',
    }), [darkMode]);
}
