// The capabilities that open a view. `talk` and `docling` only add to Reader or Chat: someone holding just those
// has nowhere to land, so they see the no-access screen rather than an endless Loading….
const VIEW_CAPABILITIES = ['reader', 'chat', 'admin'];

export function hasAccess(user) {
  return (user?.capabilities ?? []).some((cap) => VIEW_CAPABILITIES.includes(cap));
}
