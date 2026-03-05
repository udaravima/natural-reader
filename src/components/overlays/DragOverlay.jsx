import { Upload } from 'lucide-react';

export default function DragOverlay({ isDragging, theme }) {
    if (!isDragging) return null;

    return (
        <div className="fixed inset-0 z-[100] bg-blue-600/20 backdrop-blur-sm flex items-center justify-center pointer-events-none">
            <div className={`${theme.bgSecondary} rounded-2xl p-12 shadow-2xl border-4 border-dashed border-blue-500 flex flex-col items-center gap-4`}>
                <Upload size={64} className="text-blue-500 animate-bounce" />
                <p className={`text-xl font-bold text-blue-600`}>Drop PDF to Open</p>
            </div>
        </div>
    );
}
