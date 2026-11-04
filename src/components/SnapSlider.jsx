import { useEffect, useRef } from 'react';
import { magnet, stepTo, fineStep, pulls } from '../lib/snap';

const PULL_PX = 6;        // how far, on the track, a round value pulls the thumb
const HOME_PULL_PX = 9;   // a home value (1× speed) pulls further
const KEYS = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 };
const PAGE = { PageUp: 1, PageDown: -1 };
const PAGE_STEPS = 5;   // round values a Page key moves, as a native slider's larger step

/**
 * A range input that drags linearly but is pulled onto round values (lib/snap.js has the why and the rules). It is a
 * real <input type="range">, so it keeps the browser's touch handling and its slider role. Three ways in:
 * - a drag (a pointer is down on it): the value under the pointer, pulled onto the round value near it;
 * - an arrow key: the next round value, or with Shift the next fine step; Home and End: the ends; Page Up and Page
 *   Down: five round values;
 * - anything else that changes it with no pointer down, which is a screen reader's increase or decrease: the next
 *   round value that way (the pull would otherwise put a fine step straight back).
 *
 * `every`: the round values' spacing. `fine`: the smallest step a drag can land on. `home`: values that pull further.
 * `valueText(value)`: what a screen reader says.
 */
export function SnapSlider({ label, value, onChange, min, max, fine, every, home = [], valueText, className = '' }) {
  const dragging = useRef(false);
  const grid = { min, max, fine, every };
  useEffect(() => {
    const up = () => { dragging.current = false; };
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    return () => {
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
    };
  }, []);
  const set = (next) => { if (next !== value) onChange(next); };
  return (
    <input
      // step="any": the browser reports the pointer exactly. With step={fine} it would round first, and between two
      // round values only four readings would be left, too few for the pull to leave any free.
      type="range" aria-label={label} min={min} max={max} step="any" value={value}
      aria-valuetext={valueText ? valueText(value) : undefined}
      onPointerDown={() => { dragging.current = true; }}
      onChange={(e) => {
        const raw = Number(e.target.value);
        if (dragging.current) {
          set(magnet(raw, { ...grid, home, ...pulls(e.target.clientWidth, grid, PULL_PX, HOME_PULL_PX) }));
        } else if (raw !== value) {
          set(stepTo(value, raw > value ? 1 : -1, grid));
        }
      }}
      onKeyDown={(e) => {
        if (e.key === 'Home' || e.key === 'End') {
          e.preventDefault();
          set(e.key === 'Home' ? min : max);
          return;
        }
        if (PAGE[e.key]) {
          e.preventDefault();
          let next = value;
          for (let i = 0; i < PAGE_STEPS; i += 1) next = stepTo(next, PAGE[e.key], grid);
          set(next);
          return;
        }
        const direction = KEYS[e.key];
        if (!direction) return;
        e.preventDefault();
        set(e.shiftKey ? fineStep(value, direction, grid) : stepTo(value, direction, grid));
      }}
      className={className}
    />
  );
}
