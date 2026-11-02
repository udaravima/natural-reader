# v2.5.1 — All 50 voices, dropdowns you can search, and model lists you can read

**Every Kokoro voice is available, and each one reads in its own language.** Before,
only the US and UK voices were offered, and a Spanish voice was told to read Spanish
as English. **Settings → Voice & reading → Voice** now lists all 50, grouped by
language, and Sinhala and Tamil no longer come out with stray noises in them.

**Long lists work the way you'd expect.** Past 12 entries, a dropdown opens with a
search box already focused, so you open it and type. And **Admin → Models → Chat
models offered**, which piled hundreds of models on top of each other on a wide
screen, now shows them as rows you can read and scroll.

There are no database migrations. The one thing to check before you upgrade is your
Node.js version: see [Upgrade notes](#upgrade-notes).

## Fixed

### 🔊 Voices

- **All 50 Kokoro voices.** Hindi, Spanish, French, Italian, Portuguese, Japanese and
  Chinese voices join the US and UK ones. Each previews in its own language, and the
  read-aloud extension (now 0.2.1) lists them too.
- **A voice reads in its own language.** `/v1/synthesize` and `/v1/batch_synthesize`
  asked for `en-us` for every voice.
- **Sinhala, Tamil and Hindi are read without spoken noise.** Any Kokoro voice can
  read them, because espeak switches language by itself on a script it wasn't asked
  for. Each switch left a marker such as `(si)`, and Kokoro spoke it: 2 s extra on 7 s
  of Sinhala. English audio is unchanged, sample for sample.
- **River is labelled Female:** `af_` is an American female voice.

### 🧭 Lists and navigation

- **Long dropdowns open as a popup.** Open one and type, or start typing on it. Arrow
  keys and Enter work, Esc closes it without closing the dialog around it, and it
  opens upward when there's no room below.
- **Admin → Models → Chat models offered is readable.** On a wide screen every row was
  0 px tall. Rows are 16 px now, and the list scrolls.
- **The Library opens on Projects,** the first tab, with Documents second.
- **The audiobook has its own icon,** headphones. It had the Library's.
- **A tab icon:** the header's speaker on its blue tile.

## Changed

- **Every npm package is on its latest version,** including React 19.3, PDF.js 6,
  Lucide 1, Vite 8, Vitest 5 and ESLint 10. Compared against v2.5.0 in a real browser,
  a real PDF rendered the same and 18 screens looked the same, bar a slightly redrawn
  book icon.
- **Keep PDF.js at 6.2.108 or later.** Versions from 5.6.83 up to there have a
  high-severity advisory, [CVE-2026-16633](https://github.com/advisories/GHSA-hq66-cqwq-w95j),
  where a crafted PDF can run script. No release of this project shipped those
  versions, but `npm update` on an older checkout can land on one.

## Upgrade notes

1. **Check Node.js first: 22.13 or 24 is now the minimum,** because PDF.js 6 requires
   it, and `startup.sh` refuses anything older. Running the tests needs 22.22.2 or 24.15.
2. **Rebuild both halves.** The frontend needs a new build, and the backend changed
   too, so run `./startup.sh rebuild`. On bare metal, restart the backend.
3. **Reload the read-aloud extension** to get the new voices.

No migrations, and no new environment variables.

## Known issues

- **Japanese voices read kana but not kanji,** because espeak can't read kanji.
- **The known issues in [v2.5.0](RELEASE_NOTES_v2.5.0.md#known-issues) still apply.**

Not tested: how the Chinese and Japanese voices sound, the extension's voice list in a
browser, and a real phone's on-screen keyboard with the popup.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#251---2026-11-02) · diff:
[v2.5.0…v2.5.1](https://github.com/udaravima/natural-reader/compare/v2.5.0...v2.5.1)
