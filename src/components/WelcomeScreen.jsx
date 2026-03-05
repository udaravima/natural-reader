import { Upload, BookOpen, Trash2, Library } from 'lucide-react';

export default function WelcomeScreen({
    theme,
    fileInputRef,
    recentBooks,
    openFromLibrary,
    removeFromLibrary,
}) {
    return (
        <div className={`flex flex-col items-center justify-center p-6 md:p-12 text-center gap-6 ${theme.canvasBg} min-h-[400px] md:min-h-[600px] w-full md:min-w-[500px]`}>
            <div
                className={`w-20 h-20 ${theme.bgTertiary} rounded-2xl flex items-center justify-center ${theme.textMuted} border-2 border-dashed ${theme.border} cursor-pointer hover:border-blue-400 hover:text-blue-500 transition-all`}
                onClick={() => fileInputRef.current.click()}
            >
                <Upload size={36} />
            </div>
            <div>
                <p className={`${theme.textSecondary} font-semibold mb-2 text-lg`}>Open a PDF Document</p>
                <p className={`text-sm ${theme.textMuted}`}>Click to browse or drag & drop</p>
            </div>
            <button
                onClick={() => fileInputRef.current.click()}
                className="px-6 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-md"
            >
                <Upload size={16} className="inline mr-2" />
                Choose File
            </button>

            {/* LIBRARY - Recent Books */}
            {recentBooks.length > 0 && (
                <div className={`w-full max-w-md mt-4 border-t ${theme.borderSecondary} pt-6`}>
                    <div className="flex items-center gap-2 mb-4 justify-center">
                        <Library size={18} className={theme.textMuted} />
                        <h3 className={`text-sm font-bold ${theme.textSecondary}`}>Your Library</h3>
                    </div>
                    <div className="space-y-2">
                        {recentBooks.map((book) => (
                            <button
                                key={book.fileName}
                                onClick={() => openFromLibrary(book.fileName)}
                                className={`w-full flex items-center justify-between p-3 rounded-xl ${theme.bgTertiary} ${theme.hover} transition-all group border ${theme.border}`}
                            >
                                <div className="flex items-center gap-3 text-left">
                                    <div className={`w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-cyan-600 flex items-center justify-center text-white flex-shrink-0`}>
                                        <BookOpen size={18} />
                                    </div>
                                    <div className="overflow-hidden">
                                        <p className={`font-medium text-sm ${theme.text} truncate max-w-[200px]`}>
                                            {book.fileName.replace('.pdf', '')}
                                        </p>
                                        <p className={`text-xs ${theme.textMuted}`}>
                                            {(book.size / 1024 / 1024).toFixed(1)} MB • {new Date(book.lastOpened).toLocaleDateString()}
                                        </p>
                                    </div>
                                </div>
                                <button
                                    onClick={(e) => removeFromLibrary(book.fileName, e)}
                                    className={`p-2 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity ${theme.hover} hover:text-red-500`}
                                    title="Remove from library"
                                >
                                    <Trash2 size={14} />
                                </button>
                            </button>
                        ))}
                    </div>
                    <p className={`text-[10px] ${theme.textMuted} mt-3 italic`}>
                        Last {recentBooks.length} books saved for instant resume
                    </p>
                </div>
            )}
        </div>
    );
}
