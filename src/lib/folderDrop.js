/**
 * Files from the pickers and from a drop, each with the path it was picked under. A dropped folder is walked with its
 * subfolders, except folders that would only be left out afterwards (.git, node_modules, anything hidden): a
 * checkout's .git holds thousands of files, and walking them is the slow part.
 */
import { isHiddenPart } from './uploadBatch';

export function filesFromInput(fileList) {
  return Array.from(fileList || []).map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

const fileOf = (entry) => new Promise((ok, fail) => entry.file(ok, fail));
const batchOf = (reader) => new Promise((ok, fail) => reader.readEntries(ok, fail));

async function walk(entry, out) {
  if (entry.isFile) {
    out.push({ file: await fileOf(entry), path: entry.fullPath.replace(/^\/+/, '') });
    return;
  }
  if (!entry.isDirectory || isHiddenPart(entry.name)) return;
  const reader = entry.createReader();
  // readEntries hands back a batch at a time (Chrome: at most 100) and an empty one when done.
  for (let batch = await batchOf(reader); batch.length; batch = await batchOf(reader)) {
    for (const child of batch) await walk(child, out);
  }
}

/**
 * The browser empties a DataTransfer once the drop handler returns, so the entries and the file list are taken
 * before the first await; call this from the handler itself.
 */
export async function filesFromDataTransfer(dataTransfer) {
  const entries = Array.from(dataTransfer?.items || []).map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
  const files = filesFromInput(dataTransfer?.files);
  if (!entries.length) return files;
  const out = [];
  for (const entry of entries) await walk(entry, out);
  return out;
}
