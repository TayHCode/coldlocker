import type { Env } from "./env";

export const KINDS = ["skills", "rules", "mcp_servers", "routines", "plugins", "connections"] as const;
export type Kind = (typeof KINDS)[number];
export const ITEM_KINDS = { skill: "skills", rule: "rules", routine: "routines" } as const;
export type ItemKind = keyof typeof ITEM_KINDS;
export const MAX_ITEM_BYTES = 1024 * 1024;
export const NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;
export const URI_SCHEME = "agentlocker://";
export const PROMPT_HINT_LIMIT = 50;

export interface FileRecord {
  path: string;
  sub: string | null;
  sha256: string;
  size: number;
}

export interface OmittedRecord {
  path: string;
  size?: number;
  reason: string;
}

export interface Entry {
  name?: string;
  id?: string;
  path: string;
  owner: string;
  provenance: { source_id: string; origin: string };
  sha256: string;
  description?: string;
  files?: FileRecord[];
  omitted?: OmittedRecord[];
  server?: unknown;
  metadata?: unknown;
}

export interface Index {
  schema: 1;
  revision: string;
  kinds: Record<Kind, Entry[]>;
}

export interface Pointer {
  revision: string;
}

export class HubError extends Error {
  constructor(
    readonly code: number,
    message: string
  ) {
    super(message);
  }
}

export const POINTER_KEY = "current";
export const indexKey = (revision: string) => `rev:${revision}`;
export const blobKey = (sha256: string) => `blob:${sha256}`;

export const entryName = (entry: Entry): string => entry.name ?? entry.id ?? "";

export function emptyIndex(): Index {
  return {
    schema: 1,
    revision: "",
    kinds: { skills: [], rules: [], mcp_servers: [], routines: [], plugins: [], connections: [] }
  };
}

function isIndex(value: unknown, revision: string): value is Index {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<Index>;
  return (
    candidate.schema === 1 &&
    candidate.revision === revision &&
    typeof candidate.kinds === "object" &&
    candidate.kinds !== null &&
    KINDS.every((kind) => Array.isArray(candidate.kinds?.[kind]))
  );
}

let cached: Index | undefined;

export async function loadIndex(env: Env): Promise<Index> {
  const pointer = await env.CATALOG_KV.get<Pointer>(POINTER_KEY, "json");
  if (!pointer || typeof pointer.revision !== "string") return emptyIndex();
  if (cached?.revision === pointer.revision) return cached;
  const index = await env.CATALOG_KV.get<unknown>(indexKey(pointer.revision), "json");
  if (!isIndex(index, pointer.revision)) throw new Error("Published catalog revision is missing or malformed");
  cached = index;
  return index;
}

export function findEntry(index: Index, kind: ItemKind, name: string): Entry | undefined {
  return index.kinds[ITEM_KINDS[kind]].find((entry) => entryName(entry) === name);
}

export function summarize(entry: Entry): Omit<Entry, "files" | "omitted"> {
  const { files: _files, omitted: _omitted, ...rest } = entry;
  return rest;
}

const encoder = new TextEncoder();

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

const normalizeNewlines = (text: string): string => text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
const codeOrder = (left: string, right: string): number => (left < right ? -1 : left > right ? 1 : 0);

export interface Listing {
  entry: Entry;
  files: FileRecord[];
}

export function itemListing(index: Index, kind: ItemKind, name: string): Listing {
  const entry = findEntry(index, kind, name);
  if (!entry) throw new HubError(-32602, `Unknown ${kind}: ${name}`);
  const files = [...(entry.files ?? [])];
  if (kind === "skill") {
    const main = `${entry.path}/SKILL.md`;
    if (!files.some((file) => file.path === main)) throw new HubError(-32603, `Unreadable file: ${main}`);
    files.sort((left, right) => Number(left.path !== main) - Number(right.path !== main) || codeOrder(left.path, right.path));
  } else if (files.length === 0) {
    throw new HubError(-32603, `Unreadable file: ${entry.path}`);
  }
  return { entry, files };
}

export async function verifiedTexts(env: Env, kind: ItemKind, name: string, files: FileRecord[]): Promise<Map<string, string>> {
  const texts = new Map<string, string>();
  for (const file of files) {
    const text = await env.CATALOG_KV.get(blobKey(file.sha256), "text");
    if (text === null) throw new HubError(-32603, `Unreadable file: ${file.path}`);
    if ((await sha256Hex(text)) !== file.sha256) throw new HubError(-32603, `Content hash mismatch for ${kind}: ${name}`);
    texts.set(file.path, text);
  }
  return texts;
}

export interface ItemContents {
  kind: ItemKind;
  name: string;
  files: Record<string, string>;
  omitted: OmittedRecord[];
  truncated: boolean;
  max_bytes: number;
}

export async function itemContents(env: Env, index: Index, kind: ItemKind, name: string): Promise<ItemContents> {
  const { entry, files } = itemListing(index, kind, name);
  const texts = await verifiedTexts(env, kind, name, files);
  const contents: Record<string, string> = {};
  const omitted: OmittedRecord[] = [...(entry.omitted ?? [])];
  let remaining = MAX_ITEM_BYTES;
  for (const file of files) {
    if (file.size > remaining) {
      omitted.push({ path: file.path, size: file.size, reason: "size-cap" });
      continue;
    }
    const text = normalizeNewlines(texts.get(file.path) ?? "");
    contents[file.path] = text;
    remaining -= encoder.encode(text).length;
  }
  omitted.sort((left, right) => codeOrder(left.path, right.path));
  return {
    kind,
    name,
    files: contents,
    omitted,
    truncated: omitted.some((item) => item.reason === "size-cap"),
    max_bytes: MAX_ITEM_BYTES
  };
}

const UNRESERVED = /[A-Za-z0-9_.\-~]/;

function quote(value: string): string {
  let result = "";
  for (const char of value) {
    if (char === "/" || UNRESERVED.test(char)) result += char;
    else for (const byte of encoder.encode(char)) result += `%${byte.toString(16).toUpperCase().padStart(2, "0")}`;
  }
  return result;
}

function unquote(value: string): string {
  return value.replace(/(?:%[0-9A-Fa-f]{2})+/g, (run) =>
    new TextDecoder().decode(Uint8Array.from(run.match(/%[0-9A-Fa-f]{2}/g) ?? [], (hex) => Number.parseInt(hex.slice(1), 16)))
  );
}

export function resourceUri(kind: ItemKind, name: string, sub?: string | null): string {
  const base = `${URI_SCHEME}${ITEM_KINDS[kind]}/${name}`;
  return sub ? `${base}/${quote(sub)}` : base;
}

const MIME_TYPES: Record<string, string> = {
  ".md": "text/markdown",
  ".json": "application/json",
  ".txt": "text/plain",
  ".py": "text/x-python",
  ".sh": "application/x-sh",
  ".js": "text/javascript",
  ".css": "text/css",
  ".html": "text/html",
  ".csv": "text/csv"
};

export function mimeType(sub: string): string | undefined {
  const name = sub.slice(sub.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  return dot > 0 && dot < name.length - 1 ? MIME_TYPES[name.slice(dot).toLowerCase()] : undefined;
}

export interface ResourceDescriptor {
  uri: string;
  name: string;
  size: number;
  mimeType?: string;
}

export function listResources(index: Index): ResourceDescriptor[] {
  const resources: ResourceDescriptor[] = [];
  for (const kind of Object.keys(ITEM_KINDS) as ItemKind[]) {
    for (const entry of index.kinds[ITEM_KINDS[kind]]) {
      const name = entryName(entry);
      let files: FileRecord[];
      try {
        files = itemListing(index, kind, name).files;
      } catch (failure) {
        if (failure instanceof HubError) continue;
        throw failure;
      }
      for (const file of files) {
        const resource: ResourceDescriptor = {
          uri: resourceUri(kind, name, file.sub),
          name: file.sub ? `${name}/${file.sub}` : name,
          size: file.size
        };
        const mime = mimeType(file.sub ?? file.path);
        if (mime) resource.mimeType = mime;
        resources.push(resource);
      }
    }
  }
  return resources;
}

export function parseResourceUri(uri: unknown): { kind: ItemKind; name: string; sub: string | null } {
  const invalid = new HubError(-32602, "Invalid resource URI");
  if (typeof uri !== "string" || !uri.startsWith(URI_SCHEME)) throw invalid;
  const parts = uri.slice(URI_SCHEME.length).split("/");
  const kind = (Object.keys(ITEM_KINDS) as ItemKind[]).find((candidate) => ITEM_KINDS[candidate] === parts[0]);
  if (kind === undefined || parts.length < 2 || !NAME_PATTERN.test(parts[1])) throw invalid;
  const segments = parts.slice(2).map(unquote);
  if (segments.some((segment) => segment.includes("/"))) throw invalid;
  if ((kind === "rule" && segments.length > 0) || (kind === "routine" && segments.length !== 1) || (kind === "skill" && segments.length === 0)) {
    throw invalid;
  }
  return { kind, name: parts[1], sub: segments.join("/") || null };
}

export async function readResource(env: Env, index: Index, uri: unknown) {
  const { kind, name, sub } = parseResourceUri(uri);
  const { files } = itemListing(index, kind, name);
  const file = files.find((candidate) => candidate.sub === sub);
  if (!file) throw new HubError(-32602, "Unknown resource");
  const texts = await verifiedTexts(env, kind, name, files);
  return {
    uri: uri as string,
    mimeType: mimeType(sub ?? file.path) ?? "text/plain",
    text: normalizeNewlines(texts.get(file.path) ?? "")
  };
}

export async function promptText(env: Env, index: Index, name: string): Promise<{ entry: Entry; text: string }> {
  const { entry, files } = itemListing(index, "skill", name);
  const texts = await verifiedTexts(env, "skill", name, files);
  const [main, ...others] = files;
  let text = normalizeNewlines(texts.get(main.path) ?? "");
  if (others.length > 0) {
    text += "\n\n---\nSupporting files for this skill are available as MCP resources (resources/read):\n";
    text += others
      .slice(0, PROMPT_HINT_LIMIT)
      .map((file) => `- ${resourceUri("skill", name, file.sub)}\n`)
      .join("");
    if (others.length > PROMPT_HINT_LIMIT) text += `- and ${others.length - PROMPT_HINT_LIMIT} more (see resources/list)\n`;
  }
  return { entry, text };
}
