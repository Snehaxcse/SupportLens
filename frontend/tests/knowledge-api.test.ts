import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../src/lib/api/client";

afterEach(() => vi.unstubAllGlobals());

it("rejects evidence for a different ticket", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ticket_id: "another-ticket",
          items: [],
        }),
      ),
    ),
  );
  const api = new ApiClient("http://127.0.0.1:8000", () => ({
    getIdToken: async () => "synthetic",
  }));
  await expect(
    api.ticketKnowledge("requested-ticket", "hybrid"),
  ).rejects.toMatchObject({ code: "invalid_projection" });
});

it("sends the observed article generation through the authenticated client", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ status: "PUBLISHED" })));
  vi.stubGlobal("fetch", fetcher);
  const api = new ApiClient("http://127.0.0.1:8000", () => ({
    getIdToken: async () => "synthetic",
  }));
  await api.articleLifecycle("article-id", {
    operation: "PUBLISH",
    expected_generation: 2,
  });
  expect(fetcher).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/v1/agent/knowledge/article-id/lifecycle",
    expect.objectContaining({
      method: "POST",
      body: '{"operation":"PUBLISH","expected_generation":2}',
      headers: expect.objectContaining({ Authorization: "Bearer synthetic" }),
    }),
  );
});
