import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "@/lib/api/client";

const origin = "http://127.0.0.1:8000";
const ticket = "10000000-0000-4000-8000-000000000001";
const run = "10000000-0000-4000-8000-000000000002";
const request = "10000000-0000-4000-8000-000000000003";
const path = `${origin}/api/v1/agent/tickets/${ticket}/drafts`;
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const client = () =>
  new ApiClient(origin, () => ({
    uid: "synthetic-agent",
    getIdToken: vi.fn().mockResolvedValue("synthetic-token"),
  }));

afterEach(() => vi.unstubAllGlobals());

describe("grounded draft API boundary", () => {
  it("rejects a cross-ticket run in the agent draft list", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        json({ ticket_id: ticket, items: [{ ticket_id: "other", id: run }] }),
      );
    vi.stubGlobal("fetch", fetch);

    await expect(client().drafts(ticket, 2)).rejects.toMatchObject({
      code: "invalid_projection",
    });
    expect(fetch.mock.calls[0][0]).toBe(`${path}?page=2`);
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe(
      "Bearer synthetic-token",
    );
  });

  it("retains the idempotency key when retrying after a token refresh", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json({ ticket_id: ticket, id: run }, 202));
    vi.stubGlobal("fetch", fetch);
    const body = { request_id: request, expected_run_id: null };

    await client().generateDraft(ticket, body);

    expect(
      fetch.mock.calls.map(([url, options]) => [url, options.body]),
    ).toEqual([
      [path, JSON.stringify(body)],
      [path, JSON.stringify(body)],
    ]);
  });

  it("uses only the review route and rejects a mismatched approval response", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(json({ ticket_id: ticket, id: "other" }));
    vi.stubGlobal("fetch", fetch);

    await expect(
      client().reviewDraft(ticket, run, {
        request_id: request,
        expected_version: 2,
        action: "APPROVE",
      }),
    ).rejects.toMatchObject({ code: "invalid_projection" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe(`${path}/${run}/review`);
  });
});
