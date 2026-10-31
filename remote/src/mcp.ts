import {
  HubError,
  ITEM_KINDS,
  KINDS,
  NAME_PATTERN,
  URI_SCHEME,
  entryName,
  itemContents,
  itemListing,
  listResources,
  loadIndex,
  promptText,
  readResource,
  summarize,
  type Index,
  type ItemKind,
  type Kind
} from "./catalog";
import type { Env } from "./env";

export const PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"] as const;

const TOOLS: Record<string, Kind> = Object.fromEntries(KINDS.map((kind) => [`hub_list_${kind}`, kind]));
const SEARCH_TOOL = "hub_search_skills";
const SEARCH_QUERY_MAX = 500;
const SEARCH_LIMIT = 3;
const SEARCH_MIN_SCORE = 2;
const STOPWORDS = new Set(["a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could", "did", "do", "does", "for", "from", "get", "got", "had", "has", "have", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on", "or", "our", "please", "so", "than", "that", "the", "their", "then", "there", "these", "they", "this", "those", "to", "up", "us", "was", "we", "were", "what", "when", "where", "which", "who", "will", "with", "would", "you", "your"]);
const CONCEPTS: Record<string, string[]> = {
  "robot": ["agent"],
  "bot": ["agent"],
  "ai": ["agent"],
  "assistant": ["agent"],
  "model": ["agent"],
  "remember": ["lesson", "encod"],
  "forget": ["lesson", "encod"],
  "mistak": ["lesson", "correction"],
  "error": ["failure"],
  "bug": ["failure"],
  "broken": ["failure"],
  "fail": ["failure"],
  "slow": ["performance"],
  "fast": ["performance"],
  "speed": ["performance"],
  "test": ["verif"],
  "check": ["verif"],
  "prove": ["verif", "evidence"],
  "proof": ["verif", "evidence"],
  "finish": ["done"],
  "complete": ["done"],
  "tri": ["prove", "verif"]
};

const TOOL_DESCRIPTIONS: Record<Kind, string> = {
  skills:
    "List every skill in the agentlocker hub with name, description, owner, provenance and content hash. " +
    "Use hub_search_skills first for a short list; call this to see every skill, then load one with hub_get_item " +
    "(kind 'skill') only when its description matches the task. An entry with an 'error' field could not be fully read.",
  rules:
    "List the hub's rule fragments (shared policy and project instructions) with name, owner, provenance and " +
    "content hash. Read the applicable ones, including 'shared-policy', with hub_get_item (kind 'rule') before " +
    "starting work.",
  mcp_servers:
    "List nonsecret MCP server definitions indexed in the hub: transport, command and args or URL, and env " +
    "or header names with secret:// references. Secret values are never returned and listing never starts " +
    "a server. Use it to see which upstream servers exist and which credentials they need.",
  routines:
    "List routine definitions (repeatable agent jobs) with id, description, owner and provenance. Routines are " +
    "recorded definitions only: they are disabled and this endpoint never runs them. Read one with " +
    "hub_get_item (kind 'routine').",
  plugins:
    "List nonsecret plugin metadata records imported into the hub (an empty list when none were imported). " +
    "Read-only: nothing is installed or executed.",
  connections:
    "List nonsecret connection metadata records (provider and endpoint of a linked service) imported into " +
    "the hub, with secret:// references only. Read-only: nothing is contacted."
};

const SEARCH_DESCRIPTION =
  "Find skills by describing the task in a few words. Returns the best three matches (same fields as hub_list_skills), " +
  "'searched' (skills considered) and 'remaining' (further matches not shown). An empty 'results' list means no skill " +
  "matched well enough, not that the catalog is unavailable: fall back to hub_list_skills. Load a match with " +
  "hub_get_item (kind 'skill') only when its description fits the task.";

const GET_ITEM_DESCRIPTION =
  "Load one skill, rule or routine by name after finding it with hub_search_skills or the matching hub_list_* tool. For a skill, 'files' maps " +
  "every relative path (SKILL.md plus references/, scripts/ and assets/) to its UTF-8 text. Files that are not text, " +
  "symlinks and anything past the 1 MiB text cap are named in 'omitted' with their size and reason, and 'truncated' is " +
  "true when the cap cut content. Fetch individual ordinary files within 1 MiB with resources/read. A rule is one file; a routine is its " +
  "routine.json plus its prompt file. The indexed sha256 is verified before anything is returned.";

const RESOURCE_TEMPLATES = [
  {
    uriTemplate: `${URI_SCHEME}skills/{name}/{+path}`,
    name: "skill-file",
    description: "A file inside a hub skill, such as SKILL.md or references/<file>."
  },
  { uriTemplate: `${URI_SCHEME}rules/{name}`, name: "rule", description: "A hub rule fragment." },
  {
    uriTemplate: `${URI_SCHEME}routines/{id}/{file}`,
    name: "routine-file",
    description: "A routine's routine.json or its prompt file."
  }
];

type Json = Record<string, unknown>;
type Response_ = Json | null;

const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);
const isEmptyObject = (value: unknown): boolean => isObject(value) && Object.keys(value).length === 0;
const sameKeys = (value: Json, keys: string[]): boolean => {
  const present = Object.keys(value);
  return present.length === keys.length && keys.every((key) => key in value);
};
const failure = (code: number, message: string): Json => ({ error: { code, message } });

function toolDefinitions(): Json[] {
  const empty = { type: "object", properties: {}, additionalProperties: false };
  const tools: Json[] = Object.entries(TOOLS).map(([name, kind]) => ({
    name,
    description: TOOL_DESCRIPTIONS[kind],
    inputSchema: empty,
    annotations: { readOnlyHint: true }
  }));
  tools.push({
    name: SEARCH_TOOL,
    description: SEARCH_DESCRIPTION,
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          minLength: 1,
          maxLength: SEARCH_QUERY_MAX,
          description: "The task in a few plain words, such as 'monitor pull request CI'."
        }
      },
      required: ["query"],
      additionalProperties: false
    },
    annotations: { readOnlyHint: true }
  });
  tools.push({
    name: "hub_get_item",
    description: GET_ITEM_DESCRIPTION,
    inputSchema: {
      type: "object",
      properties: {
        kind: {
          type: "string",
          enum: Object.keys(ITEM_KINDS),
          description: "What to load: 'skill', 'rule' or 'routine'."
        },
        name: {
          type: "string",
          pattern: "^[a-z0-9][a-z0-9-]{0,62}$",
          description: "Entry name exactly as returned by the matching hub_list_* tool (a routine's id)."
        }
      },
      required: ["kind", "name"],
      additionalProperties: false
    },
    annotations: { readOnlyHint: true }
  });
  return tools;
}

const isItemKind = (value: unknown): value is ItemKind => typeof value === "string" && Object.hasOwn(ITEM_KINDS, value);

function validToolCall(name: unknown, args: unknown): boolean {
  if (name === "hub_get_item") {
    if (!isObject(args) || !sameKeys(args, ["kind", "name"])) return false;
    if (typeof args.kind === "object" && args.kind !== null) throw new TypeError("unhashable kind");
    return isItemKind(args.kind) && typeof args.name === "string" && NAME_PATTERN.test(args.name);
  }
  if (name === SEARCH_TOOL) {
    return isObject(args) && sameKeys(args, ["query"]) && typeof args.query === "string" && [...args.query].length > 0 && [...args.query].length <= SEARCH_QUERY_MAX;
  }
  return typeof name === "string" && Object.hasOwn(TOOLS, name) && isEmptyObject(args);
}

function stem(word: string): string {
  let stemmed = word;
  for (const suffix of ["ing", "ly", "ed", "es", "s"]) {
    if (stemmed.length > suffix.length + 2 && stemmed.endsWith(suffix)) {
      stemmed = stemmed.slice(0, -suffix.length);
      break;
    }
  }
  return stemmed.length > 3 && stemmed.endsWith("e") ? stemmed.slice(0, -1) : stemmed;
}

function terms(text: string): Set<string> {
  const words = text.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  return new Set(words.filter((word) => !STOPWORDS.has(word)).map(stem));
}

function searchSkills(catalog: Json[], query: string): Json {
  const wanted = terms(query);
  for (const word of [...wanted]) {
    for (const related of Object.hasOwn(CONCEPTS, word) ? CONCEPTS[word] : []) wanted.add(stem(related));
  }
  const overlap = (found: Set<string>): number => [...found].filter((word) => wanted.has(word)).length;
  const scored: { score: number; entry: Json }[] = [];
  for (const entry of catalog) {
    const score = 3 * overlap(terms(String(entry.name).replaceAll("-", " "))) + overlap(terms(String(entry.description ?? "")));
    if (score >= SEARCH_MIN_SCORE) scored.push({ score, entry });
  }
  scored.sort((a, b) => b.score - a.score || (a.entry.name as string < (b.entry.name as string) ? -1 : 1));
  return {
    results: scored.slice(0, SEARCH_LIMIT).map((row) => row.entry),
    searched: catalog.length,
    remaining: Math.max(0, scored.length - SEARCH_LIMIT)
  };
}

async function callTool(env: Env, index: () => Promise<Index>, name: string, args: Json): Promise<unknown> {
  const loaded = await index();
  if (name === "hub_get_item") return itemContents(env, loaded, args.kind as ItemKind, args.name as string);
  if (name === SEARCH_TOOL) return searchSkills(loaded.kinds.skills.map(summarize) as Json[], args.query as string);
  return loaded.kinds[TOOLS[name]].map(summarize);
}

async function getPrompt(env: Env, index: Index, params: Json): Promise<Json> {
  const name = params.name;
  const extras = Object.keys(params).filter((key) => key !== "name" && key !== "arguments");
  if (extras.length > 0 || typeof name !== "string" || !NAME_PATTERN.test(name) || ("arguments" in params && !isEmptyObject(params.arguments))) {
    throw new HubError(-32602, "Invalid prompt name or arguments");
  }
  let prompt;
  try {
    prompt = await promptText(env, index, name);
  } catch (caught) {
    if (caught instanceof HubError && caught.code === -32602) throw new HubError(-32602, `Unknown prompt: ${name}`);
    throw caught;
  }
  const result: Json = { messages: [{ role: "user", content: { type: "text", text: prompt.text } }] };
  return prompt.entry.description === undefined ? result : { description: prompt.entry.description, ...result };
}

function listPrompts(index: Index): Json[] {
  return index.kinds.skills.map((entry) => {
    const prompt: Json = { name: entryName(entry) };
    if (entry.description !== undefined) prompt.description = entry.description;
    prompt.arguments = [];
    return prompt;
  });
}

async function route(env: Env, index: () => Promise<Index>, method: string, params: Json): Promise<Json> {
  switch (method) {
    case "initialize": {
      const requested = params.protocolVersion;
      const version = (PROTOCOL_VERSIONS as readonly unknown[]).includes(requested) ? requested : PROTOCOL_VERSIONS[0];
      return {
        protocolVersion: version,
        capabilities: { tools: {}, prompts: {}, resources: {} },
        serverInfo: { name: "agentlocker", version: "1.0.0" }
      };
    }
    case "ping":
      return {};
    case "tools/list":
      return { tools: toolDefinitions() };
    case "tools/call": {
      const args = "arguments" in params ? params.arguments : {};
      if (!validToolCall(params.name, args)) throw new HubError(-32602, "Invalid tool or arguments");
      const values = await callTool(env, index, params.name as string, args as Json);
      return { content: [{ type: "text", text: JSON.stringify(values) }], isError: false };
    }
    case "prompts/list":
      return { prompts: listPrompts(await index()) };
    case "prompts/get":
      return getPrompt(env, await index(), params);
    case "resources/list":
      return { resources: listResources(await index()) };
    case "resources/read": {
      if (!sameKeys(params, ["uri"])) throw new HubError(-32602, "Invalid params");
      return { contents: [await readResource(env, await index(), params.uri)] };
    }
    case "resources/templates/list":
      return { resourceTemplates: RESOURCE_TEMPLATES };
    default:
      throw new HubError(-32601, "Method not found");
  }
}

export async function dispatch(request: unknown, env: Env, index: () => Promise<Index> = () => loadIndex(env)): Promise<Response_> {
  const invalid = { jsonrpc: "2.0", id: null, ...failure(-32600, "Invalid Request") };
  if (!isObject(request) || request.jsonrpc !== "2.0" || typeof request.method !== "string") return invalid;
  if (!("id" in request)) return null;
  const identity = request.id;
  if (typeof identity === "boolean" || (typeof identity === "object" && identity !== null)) return invalid;
  const params = "params" in request ? request.params : {};
  const response: Json = { jsonrpc: "2.0", id: identity };
  try {
    if (!isObject(params)) Object.assign(response, failure(-32602, "Invalid params"));
    else response.result = await route(env, index, request.method, params);
  } catch (caught) {
    if (caught instanceof HubError) Object.assign(response, failure(caught.code, caught.message));
    else if (caught instanceof SyntaxError) Object.assign(response, failure(-32603, "Malformed file in hub catalog"));
    else Object.assign(response, failure(-32603, "Hub catalog unavailable"));
  }
  return response;
}

const MAX_BODY_BYTES = 1024 * 1024;
const JSON_HEADERS = { "content-type": "application/json" };

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...headers } });
}

export async function handleMcp(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") return new Response(null, { status: 405, headers: { allow: "POST" } });
  const origin = request.headers.get("origin");
  if (origin !== null && origin !== new URL(env.PUBLIC_ORIGIN).origin) return new Response(null, { status: 403 });
  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > MAX_BODY_BYTES) return new Response(null, { status: 413 });
  let message: unknown;
  try {
    const body = await request.text();
    if (new TextEncoder().encode(body).length > MAX_BODY_BYTES) return new Response(null, { status: 413 });
    message = JSON.parse(body);
  } catch {
    return json({ jsonrpc: "2.0", id: null, ...failure(-32700, "Parse error") }, 400);
  }
  const response = await dispatch(message, env);
  return response === null ? new Response(null, { status: 202 }) : json(response);
}
