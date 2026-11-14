import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        environment: 'jsdom',
        globals: true,
        setupFiles: ['./src/test/setup.js'],
        css: false,
        // A test's whole budget, above the 5 s a single wait may take (src/test/setup.js). Vitest's default
        // is 5 s, which a multi-step journey with several waits can pass on a busy machine.
        testTimeout: 15000,
    },
});
