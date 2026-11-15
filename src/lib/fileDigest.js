/**
 * What the upload plan needs from each file: its SHA-256 and its first five bytes. `crypto.subtle` exists only on
 * HTTPS and localhost; on plain HTTP there is no hash, the plan matches by name only, and the upload route still
 * answers `unchanged` for a file it already has. The digest itself runs inside the browser's crypto, not on the page's
 * script thread; files are read one at a time by the caller, so memory holds one file.
 */
import { sha256Hex } from '../utils/docHash';

export function canHash() {
  return typeof globalThis.crypto?.subtle?.digest === 'function';
}

export async function readHead(file) {
  return new Uint8Array(await file.slice(0, 5).arrayBuffer());
}

function toBase64(bytes) {
  return btoa(String.fromCharCode(...bytes));
}

export async function digestFile(file) {
  const head = toBase64(await readHead(file));
  const sha256 = canHash() ? await sha256Hex(await file.arrayBuffer()) : null;
  return { sha256, head };
}
