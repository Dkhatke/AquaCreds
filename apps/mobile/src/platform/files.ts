/**
 * Evidence files. The camera writes to the cache directory, which Android may purge under storage
 * pressure, so a captured photo is MOVED (same bytes, no re-encode) into documents/evidence/ before
 * it is hashed and queued.
 */
import { Directory, File, Paths } from "expo-file-system";
import { t } from "../i18n";
import { LocalItemError } from "../outbox/machine";

function evidenceDir(): Directory {
  const dir = new Directory(Paths.document, "evidence");
  if (!dir.exists) dir.create({ intermediates: true, idempotent: true });
  return dir;
}

/** Move a camera capture into durable app storage; returns the new file:// URI. */
export async function persistCapture(cacheUri: string, name: string): Promise<string> {
  const source = new File(cacheUri);
  const target = new File(evidenceDir(), name);
  await source.move(target);
  return target.uri;
}

export async function readFileBytes(uri: string): Promise<Uint8Array> {
  const file = new File(uri);
  if (!file.exists) throw new LocalItemError(t("errors.fileMissing"));
  return file.bytes();
}

export async function fileSize(uri: string): Promise<number> {
  return new File(uri).size;
}

export async function deleteFile(uri: string): Promise<void> {
  const file = new File(uri);
  if (file.exists) file.delete();
}
