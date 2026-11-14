import '@testing-library/jest-dom/vitest';
import { configure } from '@testing-library/react';

// How long findBy* and waitFor keep looking before they fail. Testing Library's default is 1 s, and a test
// that renders a long list (People's "Show more" adds 50 rows to 50) can take longer than that in jsdom on a
// busy or small machine (CI's runner has 4 cores) while the code is fine. A wait still ends the moment its
// condition holds, so a passing run is no slower; only a real failure takes the full 5 s to report.
configure({ asyncUtilTimeout: 5000 });
