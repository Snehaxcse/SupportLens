"use client";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { safeError, type ApiError } from "./api/client";

const subscribeHydration = () => () => {};
const clientSnapshot = () => true;
const serverSnapshot = () => false;
// A route can hydrate after Firebase has updated the parent context. Its first
// client render must still match the server's checking state.
export function useHydrated() {
  return useSyncExternalStore(
    subscribeHydration,
    clientSnapshot,
    serverSnapshot,
  );
}

export function useResource<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  scope = key,
) {
  const [revision, revise] = useState(0);
  const [state, set] = useState<{
    key: string;
    scope: string;
    revision: number;
    data: T | null;
    error: ApiError | null;
  }>({ key: "", scope: "", revision: -1, data: null, error: null });
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted)
          set({ key, scope, revision, data, error: null });
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          const e = safeError(error);
          set((previous) => ({
            key,
            scope,
            revision,
            data:
              (e.status === 0 || e.status >= 500) && previous.scope === scope
                ? previous.data
                : null,
            error: e,
          }));
        }
      });
    return () => controller.abort();
  }, [key, scope, load, revision]);
  const reload = useCallback(() => revise((value) => value + 1), []);
  return {
    data: state.scope === scope ? state.data : null,
    error:
      state.key === key && state.revision === revision ? state.error : null,
    loading: state.key !== key || state.revision !== revision,
    reload,
  };
}
export function useMutation() {
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const run = useCallback(
    async <T>(action: () => Promise<T>): Promise<T | undefined> => {
      if (lock.current) return undefined;
      lock.current = true;
      setBusy(true);
      setError(null);
      try {
        return await action();
      } catch (error) {
        setError(safeError(error));
        return undefined;
      } finally {
        lock.current = false;
        setBusy(false);
      }
    },
    [],
  );
  const clearError = useCallback(() => {
    if (!lock.current) setError(null);
  }, []);
  return { busy, error, run, clearError };
}
