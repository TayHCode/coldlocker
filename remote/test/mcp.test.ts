import { env } from "cloudflare:test";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { dispatch } from "../src/mcp";
import {
  ORIGIN,
  completeFlow,
  expandRepeats,
  fixtureByName,
  get,
  goldenFixtures,
  installAccessCerts,
  mcpPost,
  seedFixture,
  sha256Hex,
  type GoldenCase
} from "./helpers";

let token = "";

beforeAll(async () => {
  await seedFixture(env, fixtureByName("main"));
  await installAccessCerts(env);
  token = (await completeFlow(env)).accessToken;
});

afterAll(async () => {
  await seedFixture(env, fixtureByName("main"));
});

function unwrapToolText(response: any): any {
  const content = response?.result?.content;
  if (Array.isArray(content) && content[0]?.type === "text" && "isError" in response.result) {
    return { ...response, result: { ...response.result, content: [{ ...content[0], text: JSON.parse(content[0].text) }] } };
  }
  return response;
}

const expectedFor = (golden: GoldenCase) => expandRepeats(golden.expected);

describe.each(goldenFixtures.map((fixture) => fixture.name))("local dispatch parity (%s fixture)", (name) => {
  const fixture = fixtureByName(name);

  beforeAll(async () => {
    await seedFixture(env, fixture);
  });

  it.each(fixture.cases.map((golden) => [golden.name, golden] as const))("%s via dispatch", async (_label, golden) => {
    const actual = await dispatch(golden.request, env);
    expect(unwrapToolText(actual)).toEqual(expectedFor(golden));
  });

  it.each(fixture.cases.map((golden) => [golden.name, golden] as const))("%s via authenticated /mcp", async (_label, golden) => {
    const response = await mcpPost(env, token, golden.request);
    if (golden.expected === null) {
      expect(response.status).toBe(202);
      expect(await response.text()).toBe("");
      return;
    }
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(unwrapToolText(await response.json())).toEqual(expectedFor(golden));
  });
});

describe("tool surface", () => {
  it("exposes exactly the local tools, all read-only, with no search or fetch", async () => {
    await seedFixture(env, fixtureByName("main"));
    const response: any = await dispatch({ jsonrpc: "2.0", id: 1, method: "tools/list" }, env);
    const names = response.result.tools.map((tool: any) => tool.name);
    expect(names).toEqual([
      "hub_list_skills",
      "hub_list_rules",
      "hub_list_mcp_servers",
      "hub_list_routines",
      "hub_list_plugins",
      "hub_list_connections",
      "hub_get_item"
    ]);
    for (const tool of response.result.tools) expect(tool.annotations).toEqual({ readOnlyHint: true });
  });

  it("returns local-style repository paths for item files", async () => {
    const response: any = await dispatch(
      { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "hub_get_item", arguments: { kind: "routine", name: "demo-routine" } } },
      env
    );
    const item = JSON.parse(response.result.content[0].text);
    expect(Object.keys(item.files)).toEqual(["routines/demo-routine/routine.json", "routines/demo-routine/prompt.md"]);
    expect(response.result.isError).toBe(false);
  });
});

describe("HTTP transport", () => {
  const ping = { jsonrpc: "2.0", id: 1, method: "ping" };

  it("rejects non-POST methods on /mcp with an Allow header", async () => {
    const response = await get(env, "/mcp", { method: "GET", headers: { Authorization: `Bearer ${token}` } });
    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe("POST");
  });

  it("answers a notification with 202 and no body", async () => {
    const response = await mcpPost(env, token, { jsonrpc: "2.0", method: "notifications/initialized" });
    expect(response.status).toBe(202);
    expect(await response.text()).toBe("");
  });

  it("reports malformed JSON as a 400 parse error", async () => {
    const response = await get(env, "/mcp", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: "{not json"
    });
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
  });

  it("refuses a browser Origin that is not the public origin", async () => {
    expect((await mcpPost(env, token, ping, { Origin: "https://evil.test" })).status).toBe(403);
    expect((await mcpPost(env, token, ping, { Origin: ORIGIN })).status).toBe(200);
  });

  it("refuses a body over 1 MiB", async () => {
    const response = await mcpPost(env, token, { ...ping, params: { padding: "x".repeat(1024 * 1024 + 1) } });
    expect(response.status).toBe(413);
  });

  it("requires a bearer token", async () => {
    const response = await get(env, "/mcp", { method: "POST", body: JSON.stringify(ping) });
    expect(response.status).toBe(401);
    expect(response.headers.get("www-authenticate")).toContain("resource_metadata=");
  });
});

describe("integrity and availability", () => {
  const getRule = { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "hub_get_item", arguments: { kind: "rule", name: "shared-policy" } } };

  it("returns an empty catalog before anything is published", async () => {
    await env.CATALOG_KV.delete("current");
    const response: any = await dispatch({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "hub_list_skills", arguments: {} } }, env);
    expect(JSON.parse(response.result.content[0].text)).toEqual([]);
    await seedFixture(env, fixtureByName("main"));
  });

  it("refuses a file whose stored text no longer matches its published hash", async () => {
    const main = fixtureByName("main");
    const rule = main.index.kinds.rules.find((entry) => entry.name === "shared-policy");
    const digest = rule?.files?.[0].sha256 ?? "";
    expect(await sha256Hex(expandRepeats(main.blobs[digest]) as string)).toBe(digest);
    await env.CATALOG_KV.put(`blob:${digest}`, "tampered");
    expect(await dispatch(getRule, env)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      error: { code: -32603, message: "Content hash mismatch for rule: shared-policy" }
    });
    const read: any = await dispatch({ jsonrpc: "2.0", id: 2, method: "resources/read", params: { uri: "agentlocker://rules/shared-policy" } }, env);
    expect(read.error.message).toBe("Content hash mismatch for rule: shared-policy");
  });

  it("reports a missing blob as an unreadable file", async () => {
    const main = fixtureByName("main");
    const digest = main.index.kinds.rules.find((entry) => entry.name === "shared-policy")?.files?.[0].sha256 ?? "";
    await env.CATALOG_KV.delete(`blob:${digest}`);
    expect(await dispatch(getRule, env)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      error: { code: -32603, message: "Unreadable file: rules/shared-policy.md" }
    });
  });
});
