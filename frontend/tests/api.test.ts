import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  ApiClient,
  ApiError,
  queryString,
  resolveProfile,
  validOrigin,
  type TokenSource,
} from "@/lib/api/client";
import type { Profile } from "@/lib/api/types";

const profile: Profile = {
  id: "10000000-0000-4000-8000-000000000001",
  email: "synthetic@example.invalid",
  display_name: "Synthetic",
  role: "CUSTOMER",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
};
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
describe("authenticated API boundary", () => {
  it("attaches a token without cookies or request caching", async () => {
    fetchMock.mockResolvedValue(json(profile));
    const token = vi.fn().mockResolvedValue("synthetic-id-token");
    await new ApiClient("http://127.0.0.1:8000", () => ({
      getIdToken: token,
    })).me();
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      headers: { Authorization: "Bearer synthetic-id-token" },
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
    });
    expect(token).toHaveBeenCalledWith(false);
  });
  it("never sends an operational request while signed out", async () => {
    await expect(
      new ApiClient("http://localhost:8000", () => null).me(),
    ).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("refreshes exactly once after 401", async () => {
    fetchMock
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json(profile));
    const token = vi.fn().mockResolvedValue("synthetic");
    await new ApiClient("http://localhost:8000", () => ({
      getIdToken: token,
    })).me();
    expect(token.mock.calls).toEqual([[false], [true]]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("stops after a second 401 and invalidates the session", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json({}, 401)));
    const invalidate = vi.fn();
    await expect(
      new ApiClient(
        "http://localhost:8000",
        () => ({ getIdToken: vi.fn().mockResolvedValue("synthetic") }),
        invalidate,
      ).me(),
    ).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });
  it.each([403, 404, 409, 422, 500, 503])(
    "does not retry status %s",
    async (status) => {
      fetchMock.mockResolvedValue(
        json(
          {
            error: {
              code: "request_failed",
              message: "UNTRUSTED_PRIVATE_MESSAGE",
            },
          },
          status,
        ),
      );
      const client = new ApiClient("http://localhost:8000", () => ({
        getIdToken: vi.fn().mockResolvedValue("synthetic"),
      }));
      try {
        await client.me();
        throw Error("expected rejection");
      } catch (error) {
        expect(error).toBeInstanceOf(ApiError);
        expect((error as Error).message).not.toContain(
          "UNTRUSTED_PRIVATE_MESSAGE",
        );
      }
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );
  it("rejects an internal message in a customer response", async () => {
    fetchMock.mockResolvedValue(
      json({
        messages: {
          items: [{ visibility: "INTERNAL", body: "DO_NOT_DISPLAY" }],
        },
      }),
    );
    await expect(
      new ApiClient("http://localhost:8000", () => ({
        getIdToken: vi.fn().mockResolvedValue("synthetic"),
      })).customerDetail("synthetic"),
    ).rejects.toMatchObject({ code: "invalid_projection" });
  });
  it("preserves cancellation without retry", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      new ApiClient("http://localhost:8000", () => ({
        getIdToken: vi.fn(),
      })).me(controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("marks network mutation results as uncertain and never retries", async () => {
    fetchMock.mockRejectedValue(Error("private transport detail"));
    const client = new ApiClient("http://localhost:8000", () => ({
      getIdToken: vi.fn().mockResolvedValue("synthetic"),
    }));
    await expect(
      client.createTicket({ subject: "Synthetic", description: "Synthetic" }),
    ).rejects.toThrow("may have reached the server");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("sends only an empty onboarding body", async () => {
    fetchMock.mockResolvedValue(json(profile));
    await new ApiClient("http://localhost:8000", () => ({
      getIdToken: vi.fn().mockResolvedValue("synthetic"),
    })).onboard();
    expect(fetchMock.mock.calls[0][1].body).toBe("{}");
  });
  it("does not send a token after the authenticated identity changes", async () => {
    let current: TokenSource | null = null;
    const old: TokenSource = {
      uid: "synthetic-old",
      getIdToken: async () => {
        current = {
          uid: "synthetic-new",
          getIdToken: async () => "synthetic-new-token",
        };
        return "synthetic-old-token";
      },
    };
    current = old;
    await expect(
      new ApiClient("http://localhost:8000", () => current).me(),
    ).rejects.toMatchObject({ code: "session_changed" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("invalidates the session on a final non-JSON 401 too", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(new Response("not-json", { status: 401 })),
    );
    const invalidate = vi.fn();
    await expect(
      new ApiClient(
        "http://localhost:8000",
        () => ({ getIdToken: vi.fn().mockResolvedValue("synthetic") }),
        invalidate,
      ).me(),
    ).rejects.toMatchObject({ status: 401 });
    expect(invalidate).toHaveBeenCalledTimes(1);
  });
});
describe("database-backed profile resolution", () => {
  it("onboards only when explicitly required and then reads the database profile", async () => {
    const me = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(403, "onboarding_required"))
      .mockResolvedValue(profile);
    const onboard = vi.fn().mockResolvedValue({ ...profile, role: "AGENT" });
    expect((await resolveProfile({ me, onboard })).role).toBe("CUSTOMER");
    expect(me).toHaveBeenCalledTimes(2);
    expect(onboard).toHaveBeenCalledTimes(1);
  });
  it("does not re-onboard inactive accounts", async () => {
    const onboard = vi.fn();
    await expect(
      resolveProfile({
        me: vi.fn().mockRejectedValue(new ApiError(403, "account_inactive")),
        onboard,
      }),
    ).rejects.toMatchObject({ code: "account_inactive" });
    expect(onboard).not.toHaveBeenCalled();
  });
  it("rejects an inactive or unknown-role profile", async () => {
    await expect(
      resolveProfile({
        me: vi.fn().mockResolvedValue({ ...profile, is_active: false }),
        onboard: vi.fn(),
      }),
    ).rejects.toMatchObject({ status: 403 });
    await expect(
      resolveProfile({
        me: vi.fn().mockResolvedValue({ ...profile, role: "ADMIN" }),
        onboard: vi.fn(),
      }),
    ).rejects.toMatchObject({ status: 403 });
  });
});
describe("configuration and query safety", () => {
  it.each([
    "http://example.com",
    "https://user:password@example.com",
    "https://example.com/api",
    "//example.com",
    "https://example.com?token=synthetic",
  ])("rejects unsafe API origin %s", (value) =>
    expect(() => validOrigin(value)).toThrow(ApiError),
  );
  it("encodes literal subject search and false filters", () =>
    expect(
      queryString({
        search: "%_ / ?",
        unassigned_only: false,
        empty: undefined,
      }),
    ).toBe("?search=%25_+%2F+%3F&unassigned_only=false"));
});
