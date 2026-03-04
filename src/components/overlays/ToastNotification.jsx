import React from 'react';
import { Zap } from 'lucide-react';

export default function ToastNotification({ message, darkMode, onClose }) {
    if (!message) return null;

    return (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[200] animate-pulse">
            <div className={`px-6 py-3 rounded-xl shadow-2xl border ${darkMode ? 'bg-amber-900/90 border-amber-700 text-amber-100' : 'bg-amber-50 border-amber-300 text-amber-800'} flex items-center gap-3`}>
                <Zap size={18} className="text-amber-500" />
                <span className="font-medium text-sm">{message}</span>
                <button
                    onClick={onClose}
                    className="ml-2 opacity-60 hover:opacity-100 transition-opacity"
                >
                    ✕
                </button>
            </div>
        </div>
    );
}
