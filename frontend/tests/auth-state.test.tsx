import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
const mock = vi.hoisted(() => ({ user: null as unknown, observer: vi.fn() }));
vi.mock("@firebase/auth", () => ({
  onAuthStateChanged: (_auth: unknown, callback: (user: unknown) => void) => {
    mock.observer(callback);
    callback(mock.user);
    return () => {};
  },
  createUserWithEmailAndPassword: vi.fn(),
  signInWithEmailAndPassword: vi.fn(),
  sendEmailVerification: vi.fn(),
  signOut: vi.fn(),
  reload: vi.fn(),
}));
vi.mock("@/lib/firebase", () => ({
  firebaseAuth: () => ({}),
  authErrorMessage: () => "Authentication unavailable",
}));
import { AuthProvider, useAuth, roleHome } from "@/lib/auth";
function Probe() {
  const auth = useAuth();
  return (
    <div data-testid="state">
      {auth.phase}:{auth.profile?.role}
    </div>
  );
}
const profile = {
  id: "10000000-0000-4000-8000-000000000001",
  email: "synthetic@example.invalid",
  display_name: null,
  role: "CUSTOMER",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
};
beforeEach(() => {
  mock.user = null;
  mock.observer.mockClear();
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000");
});
describe("Firebase authentication state", () => {
  it("does not restore access when an old profile refresh completes after a session failure", async () => {
    mock.user = {
      uid: "synthetic",
      emailVerified: true,
      getIdToken: vi.fn().mockResolvedValue("synthetic"),
    };
    let finishProfile!: (response: Response) => void;
    const pendingProfile = new Promise<Response>((resolve) => {
      finishProfile = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(profile)))
      .mockImplementationOnce(() => pendingProfile)
      .mockImplementation(() =>
        Promise.resolve(new Response("{}", { status: 401 })),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(useAuth, { wrapper: AuthProvider });
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await act(async () => {
      await result.current.api.customerTickets().catch(() => {});
    });
    expect(result.current.phase).toBe("error");
    await act(async () => finishProfile(new Response(JSON.stringify(profile))));
    expect(result.current.phase).toBe("error");
    expect(result.current.profile).toBeNull();
  });
  it("does not call the backend when signed out", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("state")).toHaveTextContent("signed-out"),
    );
    expect(fetch).not.toHaveBeenCalled();
  });
  it("blocks onboarding and all backend calls until the email is verified", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    mock.user = { uid: "synthetic", emailVerified: false, getIdToken: vi.fn() };
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("state")).toHaveTextContent("unverified"),
    );
    expect(fetch).not.toHaveBeenCalled();
  });
  it("takes the role from the backend, ignoring client identity claims", async () => {
    mock.user = {
      uid: "synthetic",
      emailVerified: true,
      role: "AGENT",
      getIdToken: vi.fn().mockResolvedValue("synthetic"),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(profile))),
    );
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("state")).toHaveTextContent("ready:CUSTOMER"),
    );
  });
  it("does not show a workspace for an inactive account", async () => {
    mock.user = {
      uid: "synthetic",
      emailVerified: true,
      getIdToken: vi.fn().mockResolvedValue("synthetic"),
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ error: { code: "account_inactive" } }),
            { status: 403 },
          ),
        ),
    );
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("state")).toHaveTextContent("error:"),
    );
  });
  it("separates database role destinations", () => {
    expect(roleHome("CUSTOMER")).toBe("/customer");
    expect(roleHome("AGENT")).toBe("/agent");
  });
});
