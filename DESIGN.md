# Design: "Flight recorder"

The console reads like an instrument log: every agent step is a timestamped line on ruled paper, with a
duration bar and a status mark. Calm, exact, legible on a screen recording.

## Color (OKLCH-ish intent, hex in code)
- Paper `#F3EFE6`, paper-raised `#FBF8F2`, rule `#DCD5C6`
- Ink `#17150F`, ink-soft `#4A463C`, ink-faint `#6E685B`
- Signal (one accent, used for live state, primary action, flags) `#D9472B`
- Pass `#2E6B45`, Hold `#9A6A00`
- Dark theme (night recording): paper `#14130F`, raised `#1C1A15`, rule `#2F2C25`, ink `#EDE7DA`, signal `#F0603F`

## Type
- Display and headings: **Fraunces** (soft optical serif, 600) — the "log book" voice.
- UI and body: **Public Sans** — plain, institutional, very readable.
- Data (durations, tokens, ids, JSON): **JetBrains Mono** with tabular numerals. Mono is only for data.

## Shapes and depth
- Ruled lines, not cards. Rows separated by 1px rules; sections by space.
- Radius 3px on controls only. One soft shadow for the floating review panel.

## Motion
- One authored moment: a new trace row slides in from 4px below and its duration bar grows (180ms, ease-out-expo).
- Live runs show a slow pulsing signal dot. Respect `prefers-reduced-motion`.

## Icons
- Inline SVG, 1.5px stroke, 16px grid, drawn in this file set (no emoji, no icon fonts).
