// A0 §2: GitLab-style roles, lowest first.
export const ROLES = ['reader', 'contributor', 'maintainer', 'owner'];
const LABEL = { reader: 'Reader', contributor: 'Contributor', maintainer: 'Maintainer', owner: 'Owner' };
export const roleLabel = (role) => LABEL[role] || role || '';
