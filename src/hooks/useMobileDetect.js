import { useState, useEffect, useRef } from 'react';

/**
 * Detects mobile viewport and manages sidebar open/close based on breakpoint transitions.
 */
export function useMobileDetect(mobileBreakpoint, layoutMode) {
    const [isMobile, setIsMobile] = useState(() => window.innerWidth < mobileBreakpoint);
    const [sidebarOpen, setSidebarOpen] = useState(() => !(window.innerWidth < mobileBreakpoint));
    const wasMobileRef = useRef(window.innerWidth < mobileBreakpoint);

    // Computed: Effective mobile state based on layoutMode
    const effectiveIsMobile = layoutMode === 'auto' ? isMobile : layoutMode === 'mobile';

    useEffect(() => {
        const checkMobile = () => {
            const mobile = window.innerWidth < mobileBreakpoint;
            const wasMobile = wasMobileRef.current;

            // Transitioning to mobile: close sidebar
            if (mobile && !wasMobile) {
                setSidebarOpen(false);
            }
            // Transitioning to desktop: open sidebar
            else if (!mobile && wasMobile) {
                setSidebarOpen(true);
            }

            wasMobileRef.current = mobile;
            setIsMobile(mobile);
        };

        window.addEventListener('resize', checkMobile);
        return () => window.removeEventListener('resize', checkMobile);
    }, [mobileBreakpoint]);

    return { isMobile, effectiveIsMobile, sidebarOpen, setSidebarOpen };
}
