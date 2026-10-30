import { describe, it, expect, vi } from "vitest";
import {
  collectPages,
  parseInbox,
  validateText,
  transitions,
  pageIndex,
} from "@/lib/tickets";
describe("complete queue aggregation", () => {
  it("includes every page, not just the first 100 records", async () => {
    const load = vi.fn(async (n: number) => ({
      items: Array.from({ length: n === 1 ? 100 : 3 }, (_, i) => ({
        id: `synthetic-${(n - 1) * 100 + i}`,
      })),
      page: n,
      page_size: 100,
      total: 103,
    }));
    expect(await collectPages(load)).toHaveLength(103);
    expect(load).toHaveBeenCalledTimes(2);
  });
  it("handles a genuinely empty queue", async () =>
    expect(
      await collectPages(async () => ({
        items: [],
        page: 1,
        page_size: 100,
        total: 0,
      })),
    ).toEqual([]));
  it.each(["duplicate", "changed total", "missing page"])(
    "rejects incomplete data: %s",
    async (kind) => {
      const load = async (n: number) => ({
        items:
          n === 1
            ? [{ id: "one" }]
            : kind === "missing page"
              ? []
              : [{ id: kind === "duplicate" ? "one" : "two" }],
        page: n,
        page_size: 1,
        total: n === 2 && kind === "changed total" ? 3 : 2,
      });
      await expect(collectPages(load)).rejects.toMatchObject({
        code: "queue_changed",
      });
    },
  );
  it("stops between pages on cancellation", async () => {
    const controller = new AbortController();
    const load = vi.fn(async () => {
      controller.abort();
      return { items: [{ id: "one" }], page: 1, page_size: 1, total: 2 };
    });
    await expect(collectPages(load, controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(load).toHaveBeenCalledTimes(1);
  });
});
describe("ticket validation and lifecycle", () => {
  it.each(["", "  ", "\u0000hidden"])(
    "rejects blank or unsafe input %j",
    (value) => expect(validateText(value, 200)).not.toBe(""),
  );
  it("accepts multiline plain text and enforces the limit", () => {
    expect(validateText("Steps:\n\tTry playback", 200)).toBe("");
    expect(validateText("x".repeat(201), 200)).not.toBe("");
  });
  it("permits public replies only after a resolved ticket is reopened", () => {
    expect(transitions.RESOLVED).toEqual(["REOPENED"]);
    expect(transitions.OPEN).toEqual(["IN_PROGRESS", "RESOLVED"]);
  });
  it.each(["0", "-1", "10001", "nan", "1.5"])(
    "normalizes invalid page %s",
    (value) => expect(pageIndex(value)).toBe(1),
  );
  it("never combines assignment and unassigned-only filters", () => {
    const query = parseInbox(
      new URLSearchParams(
        "unassigned_only=true&assigned_agent_id=10000000-0000-4000-8000-000000000001&status=OPEN&search=%25_",
      ),
    );
    expect(query.unassigned_only).toBe(true);
    expect(query.assigned_agent_id).toBeUndefined();
    expect(query.search).toBe("%_");
  });
});
