# Changelog

All notable changes to this project will be documented in this file.

## [1.3.0] - 2026-03-05

### Added
- Health check endpoint (`GET /v1/health`) for frontend availability detection.
- Configurable API request timeouts and unlimited batch timeout toggle.
- Collapsible settings section in the sidebar.
- Duplicate synthesis request prevention (deduplicates in-flight fetches).
- Backend auto-failover with toast notification when Kokoro is unreachable.

### Changed
- **Major refactor**: Frontend split into modular React hooks (`usePdfEngine`, `useTtsEngine`, `usePersistedState`, `useKeyboardShortcuts`, `useMobileDetect`, `useTheme`).
- **Major refactor**: Backend restructured from single `server.py` into a `server/` package (`app.py`, `endpoints.py`, `model.py`, `schemas.py`).
- Entry point changed from `python server.py` to `python run.py`.
- Removed verbose TTS endpoint logging for cleaner server output.
- State management optimized — removed unused variables and refined `useState`/`useEffect` patterns.

### Fixed
- **TTS auto-continue on page change** — reading now automatically continues on the next page without needing to pause and replay. Fixed race condition where the playback loop would re-run with stale text from the previous page.
- **StrictMode double-run** — playback index update reordered to prevent React StrictMode from skipping sentences.
- Improved `useEffect` dependency linting across hooks.

## [1.2.0] - 2026-02-02

### Added
- GPU support for Kokoro TTS (CUDA, OpenVINO for Intel Arc/NPU).
- "Continue from here" and "Selective Read" features in PDF Reader.
- Batch synthesis endpoint (`/v1/batch_synthesize`) for merging audio.

### Fixed
- UI overlaps on mobile devices.
- PDF frame resizing and header layout issues.

## [0.1.0] - 2026-01-31

### Added
- Initial release
