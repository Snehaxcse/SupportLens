import { describe, it, expect, vi, beforeEach } from "vitest";
import { ApiClient } from "@/lib/api/client";
import {
  classificationData,
  classificationTicket,
} from "./classification-fixture";
const getToken = vi.fn().mockResolvedValue("synthetic-token");
const api = () =>
  new ApiClient("http://127.0.0.1:8000", () => ({
    uid: "synthetic-agent",
    getIdToken: getToken,
  }));
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
beforeEach(() => {
  getToken.mockClear();
});
describe("agent classification API boundary", () => {
  it("uses the actual agent-only route", async () => {
    const fetch = vi.fn().mockResolvedValue(json(classificationData()));
    vi.stubGlobal("fetch", fetch);
    await api().classification(classificationTicket);
    expect(fetch.mock.calls[0][0]).toBe(
      `http://127.0.0.1:8000/api/v1/agent/tickets/${classificationTicket}/classification`,
    );
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe(
      "Bearer synthetic-token",
    );
  });
  it("replays the same observed-parent body after supported token refresh", async () => {
    const run = classificationData("PENDING").current!;
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json(run));
    vi.stubGlobal("fetch", fetch);
    const parent = "10000000-0000-4000-8000-000000007000";
    await api().retryClassification(classificationTicket, {
      expected_run_id: parent,
    });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls.map(([, options]) => options.body)).toEqual([
      JSON.stringify({ expected_run_id: parent }),
      JSON.stringify({ expected_run_id: parent }),
    ]);
    expect(getToken.mock.calls.map(([refresh]) => refresh)).toEqual([
      false,
      true,
    ]);
  });
  it.each([
    "review",
    "automation",
    "calibration",
    "cross-ticket",
    "missing-score",
  ])("rejects an invalid %s projection", async (kind) => {
    const data = classificationData();
    const unsafe: Record<string, unknown> = { ...data.current };
    if (kind === "review") unsafe.requires_human_review = false;
    if (kind === "automation") unsafe.automated_actions_allowed = true;
    if (kind === "calibration") unsafe.threshold_validated = true;
    if (kind === "cross-ticket") unsafe.ticket_id = "another-ticket";
    if (kind === "missing-score") unsafe.confidence = null;
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          json({ ...data, current: unsafe, recent_runs: [unsafe] }),
        ),
    );
    await expect(
      api().classification(classificationTicket),
    ).rejects.toMatchObject({ code: "invalid_projection" });
  });
  it("does not automatically retry rate-limit or uncertain mutation errors", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        json({ error: { code: "classification_retry_rate_limited" } }, 429),
      );
    vi.stubGlobal("fetch", fetch);
    await expect(
      api().retryClassification(classificationTicket, {
        expected_run_id: null,
      }),
    ).rejects.toMatchObject({ status: 429 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
