# v1.7.2 — Tablet overflow, selection-button no-overlap, distraction-free controls

Patch release on top of v1.7.1. Three small UX fixes around responsive layout and the distraction-free mode introduced last release.

## Fixed

### Tablet / narrow-desktop header was cut off

The overflow menu (`⋯`) introduced in v1.7.1 only kicked in at `sm:` (640 px), so anything between phone and full desktop got a cramped row where some secondary buttons were silently clipped. The cutoff is now `lg:` (1024 px) on both sides — inline buttons hide at `lg:` and the dropdown shows `lg:hidden` — so phones, tablets, and squeezed desktop windows all reach every action via the dropdown, while real desktop (≥ 1024 px) keeps the full inline layout unchanged.

### Floating "Read Selection" / "Ask AI" buttons overlapped the mobile bottom nav

Their previous `bottom-6` position sat directly on top of the `MobileBottomNav` strip on phones, so taps near play / next-sentence were being intercepted. Now `bottom-24 right-4 md:bottom-6 md:right-6` — phones get clearance over the nav, desktop is unchanged.

## Changed

### Distraction-free mode keeps basic playback controls visible

The earlier bare "Exit" pill was too minimal for actual reading sessions. A new floating control pill at bottom-center carries:

- Prev sentence (`Shift + ←`) · **Play / Pause** (`Space`) · Next sentence (`Shift + →`)
- A clickable **page-number input** so you can jump pages without leaving the mode
- **Exit** (`F` also toggles)

In chat mode or with no document open the pill collapses to just the Exit button, so the layout still feels minimal there.

## Upgrade notes

- **Pure frontend.** No backend, schema, or env-var changes. `npm run build` (or restart the dev server) is enough.
- The keyboard shortcuts modal already listed `F` for distraction-free in v1.7.1 — no doc update needed.

## Full changelog

[CHANGELOG.md#172---2026-05-23](../CHANGELOG.md#172---2026-05-23) · diff: [v1.7.1…v1.7.2](https://github.com/udaravima/natural-reader/compare/v1.7.1...v1.7.2)
