import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import MembersTab from './MembersTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const can = (over = {}) => ({ edit: false, manage_members: false, manage_owners: false, file_docs: true,
  remove_docs: false, delete: false, leave: true, ...over });
const asOwner = { id: 'p1', name: 'Q3', my_role: 'owner', can: can({ edit: true, manage_members: true, manage_owners: true, remove_docs: true, delete: true }) };
const asMaintainer = { id: 'p1', name: 'Q3', my_role: 'maintainer', can: can({ edit: true, manage_members: true, remove_docs: true }) };
const asReader = { id: 'p1', name: 'Q3', my_role: 'reader', can: can({ file_docs: false }) };
const member = (over) => ({ username: null, status: 'active', added_via: 'member', added_by: null, ...over });
const members = [
  member({ user_id: 'o1', name: 'Asha Perera', username: 'asha', role: 'owner' }),
  member({ user_id: 'm1', name: 'Ben Silva', role: 'maintainer', status: 'pending' }),
  member({ user_id: 'me', name: 'Me Myself', role: 'contributor' }),
  member({ user_id: 'a1', name: 'Root Admin', role: 'owner', added_via: 'admin' }),
];

function api(over = {}) {
  return {
    members: vi.fn(async () => members),
    putMember: vi.fn(async () => ({})),
    removeMember: vi.fn(async () => null),
    lookup: vi.fn(async () => [{ id: 'n1', name: 'New Person', username: 'newp', status: 'active' }]),
    ...over,
  };
}

function mount(project, a = api()) {
  const props = { theme, api: a, project, currentUserId: 'me', showToast: vi.fn(), onRefused: vi.fn(), onChanged: vi.fn() };
  render(<MembersTab {...props} />);
  return props;
}

const row = (name) => screen.getByText(name).closest('li');

describe('MembersTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('an Owner can change any role, including to and from Owner', async () => {
    mount(asOwner);
    await screen.findByText('Asha Perera');
    const menu = screen.getByLabelText('Role for Asha Perera');
    expect([...menu.options].map((o) => o.value)).toEqual(['reader', 'contributor', 'maintainer', 'owner']);
    expect(screen.getByLabelText('Remove Asha Perera from Q3')).toBeTruthy();
  });

  it('a Maintainer manages up to Maintainer and sees Owners as plain text', async () => {
    mount(asMaintainer);
    await screen.findByText('Asha Perera');
    expect(screen.queryByLabelText('Role for Asha Perera')).toBeNull();
    expect(within(row('Asha Perera')).getByText('Owner')).toBeTruthy();
    expect([...screen.getByLabelText('Role for Ben Silva').options].map((o) => o.value))
      .toEqual(['reader', 'contributor', 'maintainer']);
  });

  it('a Reader sees roles as plain text and no Add people', async () => {
    mount(asReader);
    await screen.findByText('Asha Perera');
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: '+ Add people' })).toBeNull();
  });

  it('marks me, pending people and admin-added members, with no controls on my own row', async () => {
    mount(asOwner);
    await screen.findByText('Asha Perera');
    expect(within(row('Me Myself')).getByText('(you)')).toBeTruthy();
    expect(screen.queryByLabelText('Role for Me Myself')).toBeNull();
    expect(within(row('Ben Silva')).getByText('awaiting approval')).toBeTruthy();
    expect(within(row('Root Admin')).getByText('added by admin')).toBeTruthy();
  });

  it('changes a role and disables the role menu while the change is in flight', async () => {
    let finish;
    const a = api({ putMember: vi.fn(() => new Promise((r) => { finish = r; })) });
    const props = mount(asOwner, a);
    const menu = await screen.findByLabelText('Role for Ben Silva');
    fireEvent.change(menu, { target: { value: 'reader' } });
    expect(menu.disabled).toBe(true);
    expect(a.putMember).toHaveBeenCalledWith('p1', 'm1', 'reader');
    finish({});
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Ben Silva is now Reader.', 3000));
    expect(props.onChanged).toHaveBeenCalled();
  });

  it('adds a person found with the picker, with the chosen role', async () => {
    const a = api();
    mount(asMaintainer, a);
    await screen.findByText('Asha Perera');
    fireEvent.click(screen.getByRole('button', { name: '+ Add people' }));
    fireEvent.change(screen.getByLabelText('Find a person to add'), { target: { value: 'ne' } });
    fireEvent.click(await screen.findByRole('button', { name: /New Person/ }));
    const role = screen.getByLabelText('Role for the new member');
    expect(role.value).toBe('contributor');
    expect([...role.options].map((o) => o.value)).toEqual(['reader', 'contributor', 'maintainer']);
    fireEvent.change(role, { target: { value: 'reader' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(a.putMember).toHaveBeenCalledWith('p1', 'n1', 'reader'));
  });

  it('says a found person is already a member rather than that no account exists', async () => {
    const a = api({ lookup: vi.fn(async () => [{ id: 'o1', name: 'Asha Perera', status: 'active' }]) });
    mount(asMaintainer, a);
    await screen.findByText('Asha Perera');
    fireEvent.click(screen.getByRole('button', { name: '+ Add people' }));
    fireEvent.change(screen.getByLabelText('Find a person to add'), { target: { value: 'asha@example.com' } });
    expect(await screen.findByText('Already a member of this project.')).toBeTruthy();
    expect(screen.queryByText(/No account uses/)).toBeNull();
  });

  it('shows a failed load inline with Retry, not as "Members (0)", and Retry reloads', async () => {
    const a = api({ members: vi.fn().mockRejectedValueOnce(new Error('Could not load members.')).mockResolvedValue(members) });
    const props = mount(asOwner, a);
    expect((await screen.findByRole('alert')).textContent).toContain('Could not load members.');
    expect(screen.queryByText('Members (0)')).toBeNull();
    expect(props.showToast).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Asha Perera')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(a.members).toHaveBeenCalledTimes(2);
  });

  it('shows the last-owner notice and reloads the project on a refusal', async () => {
    const a = api({ removeMember: vi.fn(async () => { throw new Error('A project must keep at least one Owner.'); }) });
    const props = mount(asOwner, a);
    fireEvent.click(await screen.findByLabelText('Remove Asha Perera from Q3'));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('A project must keep at least one Owner.', 5000));
    expect(props.onRefused).toHaveBeenCalled();
  });

  it('shows a disabled member\'s role as plain text but still allows removal', async () => {
    const a = api({ members: vi.fn(async () => [
      ...members, member({ user_id: 'd1', name: 'Dee Gone', role: 'reader', status: 'disabled' }),
    ]) });
    mount(asOwner, a);
    await screen.findByText('Dee Gone');
    expect(screen.queryByLabelText('Role for Dee Gone')).toBeNull();
    expect(within(row('Dee Gone')).getByText('Reader')).toBeTruthy();
    expect(screen.getByLabelText('Remove Dee Gone from Q3')).toBeTruthy();
  });
});
