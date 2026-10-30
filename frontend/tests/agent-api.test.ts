import { describe, it, expect, vi, beforeEach } from "vitest";
import { ApiClient } from "@/lib/api/client";
const id = "10000000-0000-4000-8000-000000000001";
const identity = {
  uid: "synthetic-agent",
  getIdToken: vi.fn().mockResolvedValue("synthetic-token"),
};
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi
    .fn()
    .mockImplementation(() => Promise.resolve(new Response("{}")));
  vi.stubGlobal("fetch", fetchMock);
});
describe("agent mutations use the guarded backend contract", () => {
  it("keeps claim conflicts actionable without retrying", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({ error: { code: "ticket_already_assigned" } }),
          { status: 409 },
        ),
      ),
    );
    await expect(
      new ApiClient("http://localhost:8000", () => identity).claim(id),
    ).rejects.toThrow("Another agent");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("uses explicit null for unassignment", async () => {
    await new ApiClient("http://localhost:8000", () => identity).assignment(
      id,
      { assigned_agent_id: null },
    );
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      assigned_agent_id: null,
    });
  });
  it("does not inject status changes while claiming", async () => {
    await new ApiClient("http://localhost:8000", () => identity).claim(id);
    expect(fetchMock.mock.calls[0][1].body).toBe("{}");
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/claim$/);
  });
  it.each(["PUBLIC", "INTERNAL"] as const)(
    "preserves explicit %s visibility",
    async (visibility) => {
      await new ApiClient("http://localhost:8000", () => identity).agentReply(
        id,
        { body: "Synthetic text", visibility },
      );
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
        body: "Synthetic text",
        visibility,
      });
    },
  );
  it("uses PATCH for lifecycle and priority changes", async () => {
    const client = new ApiClient("http://localhost:8000", () => identity);
    await client.status(id, { status: "RESOLVED" });
    await client.priority(id, { priority: "HIGH" });
    expect(fetchMock.mock.calls.map((call) => call[1].method)).toEqual([
      "PATCH",
      "PATCH",
    ]);
  });
});
