import { createContext, useContext, useEffect, useMemo, useRef, useReducer } from 'react';

const WorkspaceContext = createContext({ workspace: null });

export function useWorkspace() {
    return useContext(WorkspaceContext);
}

function reducer(state, action) {
    switch (action.type) {
        case 'init':
            return { history: [action.path], pointer: 0 };
        case 'navigate':
            return {
                history: [...state.history.slice(0, state.pointer + 1), action.path],
                pointer: state.pointer + 1,
            };
        case 'back':
            return state.pointer > 0 ? { ...state, pointer: state.pointer - 1 } : state;
        case 'forward':
            return state.pointer < state.history.length - 1
                ? { ...state, pointer: state.pointer + 1 }
                : state;
        default:
            return state;
    }
}

export function WorkspaceProvider({ workspace, initialPath, onOpenDoc, children }) {
    const [state, dispatch] = useReducer(reducer, {
        history: initialPath ? [initialPath] : [],
        pointer: initialPath ? 0 : -1,
    });

    const onOpenRef = useRef(onOpenDoc);
    onOpenRef.current = onOpenDoc;

    // Mirror state into a ref so goBack/goForward read fresh values.
    const stateRef = useRef(state);
    stateRef.current = state;

    // Open the initial document once per workspace/initialPath.
    const openedKey = useRef(null);
    useEffect(() => {
        const key = `${workspace?.rootName}::${initialPath}`;
        if (workspace && initialPath && openedKey.current !== key) {
            openedKey.current = key;
            dispatch({ type: 'init', path: initialPath });
            onOpenRef.current?.(initialPath, { anchor: null });
        }
    }, [workspace, initialPath]);

    const navigate = (path, anchor = null) => {
        dispatch({ type: 'navigate', path });
        onOpenRef.current?.(path, { anchor });
    };

    const goBack = () => {
        const { history, pointer } = stateRef.current;
        if (pointer <= 0) return;
        dispatch({ type: 'back' });
        onOpenRef.current?.(history[pointer - 1], { anchor: null });
    };

    const goForward = () => {
        const { history, pointer } = stateRef.current;
        if (pointer >= history.length - 1) return;
        dispatch({ type: 'forward' });
        onOpenRef.current?.(history[pointer + 1], { anchor: null });
    };

    const value = useMemo(() => ({
        workspace,
        currentPath: state.pointer >= 0 ? state.history[state.pointer] : null,
        canGoBack: state.pointer > 0,
        canGoForward: state.pointer >= 0 && state.pointer < state.history.length - 1,
        navigate,
        goBack,
        goForward,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }), [workspace, state]);

    return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
