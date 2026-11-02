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
const box = () => screen.getByRole('searchbox', { name: 'Find a timezone' });
const type = (text) => fireEvent.change(box(), { target: { value: text } });
const list = () => screen.getByRole('listbox', { name: 'Timezone' });
const names = (root) => [...root.querySelectorAll('option')].map((o) => o.value);

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
    expect(screen.getByRole('combobox', { name: 'Timezone' })).toBeTruthy();
  });

  it('narrows the options and opens a list of the matches', () => {
    render(<Picker />);
    expect(screen.getByRole('combobox', { name: 'Timezone' })).toBeTruthy();
    type('america');
    expect(names(list())).toEqual(['America/New_York', 'America/Sao_Paulo', 'Asia/Colombo']);
  });

  it('never loses the value in use while searching', () => {
    render(<Picker />);
    type('nothing matches this');
    expect(list().value).toBe('Asia/Colombo');
  });

  it('a click on a match chooses it and closes the list', () => {
    const onPick = vi.fn();
    render(<Picker onPick={onPick} />);
    type('new york');
    fireEvent.change(list(), { target: { value: 'America/New_York' } });
    fireEvent.click(list());
    expect(onPick).toHaveBeenCalledWith('America/New_York');
    expect(screen.getByRole('combobox', { name: 'Timezone' }).value).toBe('America/New_York');
    expect(box().value).toBe('');
  });

  it('Enter on one match chooses it', () => {
    const onPick = vi.fn();
    render(<Picker onPick={onPick} />);
    type('new york');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onPick).toHaveBeenCalledWith('America/New_York');
  });

  it('Enter on several matches chooses nothing and submits nothing', () => {
    const onPick = vi.fn();
    const submit = vi.fn((e) => e.preventDefault());
    render(<form onSubmit={submit}><Picker onPick={onPick} /></form>);
    type('america');
    expect(fireEvent.keyDown(box(), { key: 'Enter' })).toBe(false);   // default prevented
    expect(onPick).not.toHaveBeenCalled();
  });

  it('Enter leaves things alone when the one that matches is already chosen', () => {
    const onPick = vi.fn();
    render(<Picker onPick={onPick} initial="Asia/Colombo" />);
    type('colombo');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onPick).not.toHaveBeenCalled();
    expect(box().value).toBe('');
  });

  it('Enter does not pick another option just because the chosen one also matches', () => {
    const onPick = vi.fn();
    render(<Picker onPick={onPick} initial="America/New_York" />);
    type('america');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onPick).not.toHaveBeenCalled();
  });

  it('Escape clears the search and stays inside a dialog; a second Escape closes the dialog', () => {
    const onDialogKey = vi.fn();
    render(<div onKeyDown={onDialogKey}><Picker /></div>);
    type('sao');
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(box().value).toBe('');
    expect(onDialogKey).not.toHaveBeenCalled();
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(onDialogKey).toHaveBeenCalledTimes(1);
  });

  it('shows every option again when the list stops being long', () => {
    const { rerender } = render(<Picker />);
    type('sao');
    const short = zones.slice(0, LONG_LIST);
    rerender(<Picker options={short} />);
    expect(names(screen.getByRole('combobox', { name: 'Timezone' }))).toHaveLength(LONG_LIST);
  });

  it('ArrowDown moves to the list, and Escape comes back', () => {
    render(<Picker />);
    type('america');
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    expect(document.activeElement).toBe(list());
    fireEvent.keyDown(list(), { key: 'Escape' });
    expect(document.activeElement).toBe(box());
    expect(box().value).toBe('');
  });

  it('keeps a pinned option through a search', () => {
    render(<Picker options={[{ value: 'auto', label: 'Auto', pinned: true }, ...zones]} initial="auto" />);
    type('colombo');
    expect(names(list())).toEqual(['auto', 'Asia/Colombo']);
  });

  it('keeps provider groups', () => {
    const options = many(14).map((m, i) => ({ value: m, label: m, group: i < 7 ? 'ollama' : 'cloud' }));
    render(<Picker options={options} initial="model-0" />);
    const select = screen.getByRole('combobox', { name: 'Timezone' });
    expect([...select.querySelectorAll('optgroup')].map((g) => g.label)).toEqual(['ollama', 'cloud']);
    expect(within(select).getAllByRole('option')).toHaveLength(14);
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
