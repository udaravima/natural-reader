/**
 * State that belongs to one signed-in user, e.g. an open workspace folder:
 * stored as `{ ownerId, value }` and read through `visibleTo`, so a different
 * user never sees it — at once on a user change, and even if an async
 * restore for the previous user lands late. Nothing has to be cleared.
 */
export const ownedBy = (ownerId, value) => (value == null ? null : { ownerId, value });

export const visibleTo = (owned, userId) =>
    (owned && userId && owned.ownerId === userId ? owned.value : null);
