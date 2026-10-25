import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SettingRow, SectionSaveBar } from './SettingRow';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };

const row = (over = {}) => render(
  <SettingRow theme={theme} id="tz" label="Timezone" help="Used for the budget day." source="default" envName="TZ" {...over}>
    <input id="tz" aria-describedby="tz-help tz-source" />
  </SettingRow>,
);

describe('SettingRow', () => {
  it('shows the label, the help and the control', () => {
    row();
    expect(screen.getByLabelText('Timezone')).toBeInTheDocument();
    expect(document.getElementById('tz-help')).toHaveTextContent('Used for the budget day.');
  });

  it('says where each source comes from, with the variable in code font', () => {
    const { rerender } = row({ source: 'admin' });
    const src = () => document.getElementById('tz-source');
    expect(src().textContent).toBe('Set here.');
    rerender(
      <SettingRow theme={theme} id="tz" label="Timezone" source="env" envName="TZ"><input id="tz" /></SettingRow>,
    );
    expect(src().textContent).toBe('From the server setting TZ.');
    expect(src().querySelector('code')).toHaveTextContent('TZ');
    rerender(
      <SettingRow theme={theme} id="tz" label="Timezone" source="default" envName="TZ"><input id="tz" /></SettingRow>,
    );
    expect(src().textContent).toBe('The default.');
  });

  it('offers the reset only when asked to, and calls it', () => {
    const onUseServer = vi.fn();
    const { rerender } = row({ onUseServer });
    expect(screen.queryByRole('button', { name: 'Use the server setting' })).toBeNull();
    rerender(
      <SettingRow theme={theme} id="tz" label="Timezone" source="admin" envName="TZ" canUseServer onUseServer={onUseServer}>
        <input id="tz" />
      </SettingRow>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Use the server setting' }));
    expect(onUseServer).toHaveBeenCalledTimes(1);
  });
});

describe('SectionSaveBar', () => {
  const bar = (over = {}) => render(
    <SectionSaveBar theme={theme} dirty={false} saving={false} error={null} onSave={vi.fn()} onDiscard={vi.fn()} {...over} />,
  );

  it('has Save disabled and no Discard while nothing changed', () => {
    bar();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Discard changes' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('saves and discards when something changed', () => {
    const onSave = vi.fn();
    const onDiscard = vi.fn();
    bar({ dirty: true, onSave, onDiscard });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it('shows Saving… and cannot be clicked again while a save is out', () => {
    bar({ dirty: true, saving: true });
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
  });

  it('shows the error as an alert', () => {
    bar({ dirty: true, error: 'Nope.' });
    expect(screen.getByRole('alert')).toHaveTextContent('Nope.');
  });
});
