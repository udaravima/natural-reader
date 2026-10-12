/**
 * The old-version banner rule (V1 spec §7), the same as the server's
 * doc_versions.old_version_of: the first item when the content is an old
 * version in at least one project the reader can see and current in none;
 * else null. Content current in one project and old in another is a current
 * document — its Library row says the other project moved on.
 */
export function oldVersionOf(versionsIn) {
  const list = Array.isArray(versionsIn) ? versionsIn : [];
  if (list.length === 0 || list.some((v) => v.is_current)) return null;
  return list[0];
}
