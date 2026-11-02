import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { SearchableList, SearchableSelect } from './SearchableList';
import { LONG_LIST, hits } from '../lib/listSearch';

const theme = { border: '', bg: '', text: '', textSecondary: '' };
const many = (n) => Array.from({ length: n }, (_, i) => `model-${i}`);
const zones = ['Africa/Abidjan', 'America/New_York', 'America/Sao_Paulo', 'Asia/Colombo', ...many(20)]
  .map((z) => ({ value: z, label: z }));

function Picker({ options = zones, initial = 'Asia/Colombo', onPick = () => {}, ...rest }) {
  const [value, setValue] = useState(initial);
  return (
    <SearchableSelect theme={theme} aria-label="Timezone" findLabel="Find a timezone" options={options} value={value}
      onChange={(v) => { setValue(v); onPick(v); }} {...rest} />
  );
}
const trigger = () => screen.getByRole('combobox', { name: 'Timezone' });
const open = () => fireEvent.click(trigger());
const box = () => screen.getByRole('searchbox', { name: 'Find a timezone' });
const type = (text) => fireEvent.change(box(), { target: { value: text } });
const list = () => screen.getByRole('listbox', { name: 'Timezone' });
const rows = () => within(list()).queryAllByRole('option');
const names = () => rows().map((o) => o.textContent);
const active = () => document.getElementById(box().getAttribute('aria-activedescendant'))?.textContent;

describe('hits', () => {
  it('finds across the separators and accents people do not type', () => {
    expect(hits('new york', 'America/New_York')).toBe(true);
    expect(hits('sao', 'São Paulo')).toBe(true);
    expect(hits('york new', 'America/New_York')).toBe(true);
    expect(hits('london', 'America/New_York')).toBe(false);
  });
});

describe('SearchableSelect', () => {
  it('leaves a short list exactly as it was', () => {
    const short = many(LONG_LIST).map((m) => ({ value: m, label: m }));
    render(<Picker options={short} initial="model-0" />);
    expect(screen.queryByRole('searchbox')).toBeNull();
    expect(trigger().tagName).toBe('SELECT');
  });

  it('shows one control until it is opened: no box, no list', () => {
    render(<Picker />);
    expect(trigger().textContent).toBe('Asia/Colombo');
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('searchbox')).toBeNull();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('opens with every option and the search box already focused', () => {
    render(<Picker />);
    open();
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(rows()).toHaveLength(zones.length);
    expect(document.activeElement).toBe(box());
  });

  it('opens on the option in use, marked as chosen', () => {
    render(<Picker />);
    open();
    expect(active()).toBe('Asia/Colombo');
    expect(screen.getByRole('option', { selected: true }).textContent).toBe('Asia/Colombo');
  });

  it('narrows as you type, across the separators people do not type', () => {
    render(<Picker />);
    open();
    type('new york');
    expect(names()).toEqual(['America/New_York']);
    expect(active()).toBe('America/New_York');
  });

  it('says when nothing matches', () => {
    render(<Picker />);
    open();
    type('nothing matches this');
    expect(rows()).toHaveLength(0);
    expect(screen.getByText('Nothing matches.')).toBeTruthy();
  });

  it('a click on an option chooses it, closes, and goes back to the control', () => {
    const onPick = vi.fn();
    render(<Picker onPick={onPick} />);
    open();
    type('new york');
    fireEvent.click(screen.getByRole('option', { name: 'America/New_York' }));
    expect(onPick).toHaveBeenCalledWith('America/New_York');
    expect(trigger().textContent).toBe('America/New_York');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('Enter chooses the highlighted option, and the arrows move the highlight', () => {
    const onPick = vi.fn();
    render(<Picker onPick={onPick} />);
    open();
    type('america');
    expect(active()).toBe('America/New_York');
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    expect(active()).toBe('America/Sao_Paulo');
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    expect(active()).toBe('America/Sao_Paulo');
    fireEvent.keyDown(box(), { key: 'ArrowUp' });
    expect(active()).toBe('America/New_York');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onPick).toHaveBeenCalledWith('America/New_York');
  });

  it('choosing what is already chosen changes nothing and still closes', () => {
    const onPick = vi.fn();
    render(<Picker onPick={onPick} />);
    open();
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onPick).not.toHaveBeenCalled();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('Enter submits no form around it', () => {
    const onSubmit = vi.fn((e) => e.preventDefault());
    render(<form onSubmit={onSubmit}><Picker /></form>);
    open();
    expect(fireEvent.keyDown(box(), { key: 'Enter' })).toBe(false);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('typing on the closed control starts the search', () => {
    render(<Picker />);
    trigger().focus();
    fireEvent.keyDown(trigger(), { key: 's' });
    expect(box().value).toBe('s');
    expect(document.activeElement).toBe(box());
  });

  it('the arrow keys open it too', () => {
    render(<Picker />);
    fireEvent.keyDown(trigger(), { key: 'ArrowDown' });
    expect(rows()).toHaveLength(zones.length);
  });

  it('Escape closes it and stays inside a dialog; a second Escape reaches the dialog', () => {
    const onDialogKey = vi.fn();
    render(<div role="dialog" onKeyDown={onDialogKey}><Picker /></div>);
    open();
    type('sao');
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onDialogKey).not.toHaveBeenCalled();
    fireEvent.keyDown(trigger(), { key: 'Escape' });
    expect(onDialogKey).toHaveBeenCalledTimes(1);
  });

  it('a press outside closes it and chooses nothing', () => {
    const onPick = vi.fn();
    render(<div><button>elsewhere</button><Picker onPick={onPick} /></div>);
    open();
    fireEvent.pointerDown(screen.getByText('elsewhere'));
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onPick).not.toHaveBeenCalled();
  });

  it('forgets the search when it closes', () => {
    render(<Picker />);
    open();
    type('sao');
    fireEvent.keyDown(box(), { key: 'Escape' });
    open();
    expect(box().value).toBe('');
    expect(rows()).toHaveLength(zones.length);
  });

  it('a disabled option can be seen but not chosen, and the arrows skip it', () => {
    const onPick = vi.fn();
    const options = [...zones, { value: 'Asia/Dhaka', label: 'Asia/Dhaka', disabled: true },
      { value: 'Asia/Kolkata', label: 'Asia/Kolkata' }];
    render(<Picker onPick={onPick} options={options} />);
    open();
    type('asia');
    const off = screen.getByRole('option', { name: 'Asia/Dhaka' });
    expect(off.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(off);
    expect(onPick).not.toHaveBeenCalled();
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    expect(active()).toBe('Asia/Kolkata');
  });

  it('keeps provider groups, and a group goes when none of it matches', () => {
    const options = [
      ...many(7).map((m) => ({ value: `a:${m}`, label: m, group: 'ollama' })),
      ...many(7).map((m) => ({ value: `b:${m}x`, label: `${m}x`, group: 'openrouter' })),
    ];
    render(<Picker options={options} initial="a:model-0" />);
    open();
    expect(screen.getByRole('group', { name: 'ollama' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'openrouter' })).toBeTruthy();
    type('model-1x');
    expect(screen.queryByRole('group', { name: 'ollama' })).toBeNull();
    expect(names()).toEqual(['model-1x']);
  });

  it('draws outside its panel, so a narrow box cannot squeeze or clip it', () => {
    const { container } = render(<div style={{ width: 120, overflow: 'hidden' }}><Picker /></div>);
    open();
    expect(container.contains(list())).toBe(false);
    expect(document.body.contains(list())).toBe(true);
  });

  it('stays inside a dialog, for its focus trap and for screen readers', () => {
    render(<div role="dialog"><Picker /></div>);
    open();
    expect(screen.getByRole('dialog').contains(list())).toBe(true);
  });

  it('hands back a string, as a native select does', () => {
    const onPick = vi.fn();
    const numbers = many(LONG_LIST + 1).map((_, i) => ({ value: i, label: `Option ${i}` }));
    render(<Picker onPick={onPick} options={numbers} initial={0} />);
    expect(trigger().textContent).toBe('Option 0');
    open();
    fireEvent.click(screen.getByRole('option', { name: 'Option 3' }));
    expect(onPick).toHaveBeenCalledWith('3');
  });

  it('does not open when disabled', () => {
    render(<Picker disabled />);
    open();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('passes the name and description on to the control', () => {
    render(<><label htmlFor="tz">Zone</label><Picker aria-label={undefined} id="tz" aria-describedby="help" /></>);
    const control = screen.getByRole('combobox', { name: 'Zone' });
    expect(control.getAttribute('aria-describedby')).toBe('help');
  });
});

describe('SearchableList', () => {
  const rows = (items) => items.map((m) => <span key={m}>{m}</span>);
  const mount = (items, props = {}) => render(
    <SearchableList theme={theme} label="Find a model" items={items} textOf={(m) => m} {...props}>{rows}</SearchableList>,
  );

  it('leaves a short list exactly as it was', () => {
    mount(many(LONG_LIST));
    expect(screen.queryByRole('searchbox')).toBeNull();
    expect(screen.getByText('model-3').parentElement.className).not.toContain('overflow-y-auto');
  });

  it('never lays long rows out in a flex column, which would squeeze them to nothing', () => {
    // No layout in jsdom, so this pins the cause: 424 rows measured 0 px tall in a real browser in `flex flex-col`.
    render(<SearchableList theme={theme} label="Find" items={many(LONG_LIST + 1)} textOf={(m) => m}>
      {(shown) => shown.map((m) => <label key={m} className="min-h-11 md:min-h-0">{m}</label>)}
    </SearchableList>);
    const box = screen.getByText('model-0').parentElement;
    expect(box.className).toContain('overflow-y-auto');
    expect(box.className.split(' ')).not.toContain('flex');
  });

  it('scrolls a long list in its own box and says how long it is', () => {
    mount(many(300));
    expect(screen.getByText('model-3').parentElement.className).toContain('overflow-y-auto');
    expect(screen.getByText('300 in the list')).toBeTruthy();
  });

  it('narrows the rows and counts them', () => {
    mount(many(300));
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a model' }), { target: { value: '299' } });
    expect(screen.queryByText('model-3')).toBeNull();
    expect(screen.getByText('model-299')).toBeTruthy();
    expect(screen.getByText('1 of 300')).toBeTruthy();
  });

  it('says when nothing matches', () => {
    mount(many(300));
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a model' }), { target: { value: 'zzz' } });
    expect(screen.getByText('Nothing matches.')).toBeTruthy();
  });

  it('keeps a row that has to stay on screen', () => {
    mount(many(300), { keep: (m) => m === 'model-7' });
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a model' }), { target: { value: 'model-299' } });
    expect(screen.getByText('model-7')).toBeTruthy();
  });
});
