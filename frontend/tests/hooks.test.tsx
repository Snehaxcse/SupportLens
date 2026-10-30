import { describe, it, expect, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useCallback } from "react";
import { useMutation, useResource } from "@/lib/hooks";
import { ApiError } from "@/lib/api/client";
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
describe("request ordering and mutations", () => {
  it("retains the same ticket while paginating so composers do not lose drafts", async () => {
    const first = deferred<string>(),
      second = deferred<string>();
    const { result, rerender } = renderHook(
      ({ page }: { page: number }) => {
        const load = useCallback(
          () => (page === 1 ? first.promise : second.promise),
          [page],
        );
        return useResource(`ticket:page:${page}`, load, "ticket:synthetic");
      },
      { initialProps: { page: 1 } },
    );
    await act(async () => first.resolve("ticket page one"));
    rerender({ page: 2 });
    expect(result.current.data).toBe("ticket page one");
    expect(result.current.loading).toBe(true);
    await act(async () => second.resolve("ticket page two"));
    expect(result.current.data).toBe("ticket page two");
  });
  it("does not let a stale response overwrite a newer route", async () => {
    const a = deferred<string>(),
      b = deferred<string>();
    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => {
        const load = useCallback(
          () => (id === "a" ? a.promise : b.promise),
          [id],
        );
        return useResource(id, load);
      },
      { initialProps: { id: "a" } },
    );
    rerender({ id: "b" });
    await act(async () => b.resolve("new result"));
    await act(async () => a.resolve("old result"));
    expect(result.current.data).toBe("new result");
  });
  it("keeps existing content while refreshing but clears it on forbidden", async () => {
    const first = deferred<string>(),
      second = deferred<string>();
    let calls = 0;
    const load = () => (++calls === 1 ? first.promise : second.promise);
    const { result } = renderHook(() => useResource("ticket", load));
    await act(async () => first.resolve("authorized"));
    act(() => result.current.reload());
    expect(result.current.data).toBe("authorized");
    expect(result.current.loading).toBe(true);
    await act(async () => second.reject(new ApiError(403, "account_inactive")));
    await waitFor(() => expect(result.current.data).toBeNull());
  });
  it("prevents duplicate submissions before React has rendered disabled state", async () => {
    const response = deferred<string>();
    const action = vi.fn(() => response.promise);
    const { result } = renderHook(useMutation);
    let one: Promise<string | undefined>;
    let two: Promise<string | undefined>;
    act(() => {
      one = result.current.run(action);
      two = result.current.run(action);
    });
    expect(action).toHaveBeenCalledTimes(1);
    await act(async () => response.resolve("confirmed"));
    expect(await one!).toBe("confirmed");
    expect(await two!).toBeUndefined();
    expect(result.current.busy).toBe(false);
  });
});
