import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { SnapSlider } from './SnapSlider';

function Speed({ initial = 1.1, onChange = () => {} }) {
  const [value, setValue] = useState(initial);
  return (
    <SnapSlider label="Speed" value={value} min={0.5} max={2} fine={0.01} every={0.05} home={[1]}
      valueText={(v) => `${v.toFixed(2)}×`} onChange={(v) => { setValue(v); onChange(v); }} />
  );
}
const slider = () => {
  const el = screen.getByRole('slider', { name: 'Speed' });
  Object.defineProperty(el, 'clientWidth', { value: 470, configurable: true });   // a desktop track
  return el;
};
const drag = (el, raw) => fireEvent.change(el, { target: { value: String(raw) } });

describe('SnapSlider', () => {
  it('dragged near a round value, lands on it; between round values, moves freely', () => {
    render(<Speed />);
    const el = slider();
    fireEvent.pointerDown(el);
    drag(el, 1.14);
    expect(el).toHaveValue('1.15');
    drag(el, 1.12);
    expect(el).toHaveValue('1.12');
  });

  it('pulls harder onto its home value', () => {
    render(<Speed />);
    const el = slider();
    fireEvent.pointerDown(el);
    drag(el, 1.02);   // 0.02 from 1x: the 5 px pull alone (0.016 on a 470 px track) would leave it free
    expect(el).toHaveValue('1');
  });

  it('a key steps to the next round value, and Shift with it by the fine step', () => {
    render(<Speed initial={1.12} />);
    const el = slider();
    fireEvent.keyDown(el, { key: 'ArrowRight' });
    expect(el).toHaveValue('1.15');
    fireEvent.keyDown(el, { key: 'ArrowLeft' });
    expect(el).toHaveValue('1.1');
    fireEvent.keyDown(el, { key: 'ArrowUp', shiftKey: true });
    expect(el).toHaveValue('1.11');
  });

  // A native slider's Home and End go to the ends, and Page Up and Down move by a larger step: they must still.
  it('Home and End go to the ends; Page Up and Page Down move five round values', () => {
    render(<Speed initial={1.1} />);
    const el = slider();
    fireEvent.keyDown(el, { key: 'End' });
    expect(el).toHaveValue('2');
    fireEvent.keyDown(el, { key: 'Home' });
    expect(el).toHaveValue('0.5');
    fireEvent.keyDown(el, { key: 'PageUp' });
    expect(el).toHaveValue('0.75');
    fireEvent.keyDown(el, { key: 'PageDown' });
    expect(el).toHaveValue('0.5');
  });

  // A screen reader's "increase" changes the value by the fine step, with no pointer down: the pull would put it
  // straight back, and it would never move.
  it('a change with no pointer down, as a screen reader makes, steps to the next round value', () => {
    render(<Speed initial={1.1} />);
    const el = slider();
    drag(el, 1.11);
    expect(el).toHaveValue('1.15');
    drag(el, 1.14);
    expect(el).toHaveValue('1.1');
  });

  it('once the pointer is up, the pull is off again', () => {
    render(<Speed initial={1.1} />);
    const el = slider();
    fireEvent.pointerDown(el);
    fireEvent.pointerUp(window);
    drag(el, 1.12);
    expect(el).toHaveValue('1.15');
  });

  it('says its value the way a person reads it, and reports each change once', () => {
    const onChange = vi.fn();
    render(<Speed initial={1.1} onChange={onChange} />);
    const el = slider();
    expect(el).toHaveAttribute('aria-valuetext', '1.10×');
    fireEvent.pointerDown(el);
    drag(el, 1.105);
    expect(onChange).not.toHaveBeenCalled();   // still on 1.10: nothing changed
    drag(el, 1.14);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(1.15);
  });
});
