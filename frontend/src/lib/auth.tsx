"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  reload,
  sendEmailVerification,
  signInWithEmailAndPassword,
  signOut as firebaseSignOut,
  type User,
} from "@firebase/auth";
import { ApiClient, resolveProfile, safeError } from "./api/client";
import type { Profile, Role } from "./api/types";
import { authErrorMessage, firebaseAuth } from "./firebase";

export type Phase =
  "checking" | "signed-out" | "unverified" | "ready" | "error";
type State = {
  phase: Phase;
  profile: Profile | null;
  error: string | null;
  user: User | null;
};
type AuthContextValue = State & {
  api: ApiClient;
  signIn(email: string, password: string): Promise<void>;
  signUp(email: string, password: string): Promise<void>;
  signOut(): Promise<void>;
  verifyEmail(): Promise<void>;
  refresh(): Promise<void>;
  announcement: string;
  announce(message: string): void;
};
const AuthContext = createContext<AuthContextValue | null>(null);
export function roleHome(role: Role) {
  return role === "AGENT" ? "/agent" : "/customer";
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({
    phase: "checking",
    profile: null,
    error: null,
    user: null,
  });
  const [announcement, announce] = useState("");
  const userRef = useRef<User | null>(null);
  const syncRef = useRef<
    (user: User | null, background?: boolean) => Promise<void>
  >(async () => {});
  const invalidateRef = useRef<() => void>(() => {});
  const api = useMemo(
    () =>
      new ApiClient(
        process.env.NEXT_PUBLIC_API_BASE_URL || "",
        () => userRef.current,
        (reason) => {
          invalidateRef.current();
          setState((s) => ({
            ...s,
            phase: "error",
            profile: null,
            error:
              reason?.message ||
              "Your session needs attention. Refresh your account or sign out and sign in again.",
          }));
        },
      ),
    [],
  );
  useEffect(() => {
    let active = true;
    let controller: AbortController | undefined;
    async function sync(user: User | null, background = false) {
      controller?.abort();
      const current = new AbortController();
      controller = current;
      userRef.current = user;
      if (!user) {
        if (active) {
          announce("");
          setState({
            phase: "signed-out",
            profile: null,
            error: null,
            user: null,
          });
        }
        return;
      }
      if (!user.emailVerified) {
        if (active)
          setState({ phase: "unverified", profile: null, error: null, user });
        return;
      }
      if (!background)
        setState({ phase: "checking", profile: null, error: null, user });
      try {
        const profile = await resolveProfile(api, current.signal);
        if (active && !current.signal.aborted)
          setState({ phase: "ready", profile, error: null, user });
      } catch (error) {
        if (active && !current.signal.aborted)
          setState({
            phase: "error",
            profile: null,
            error: safeError(error).message,
            user,
          });
      }
    }
    syncRef.current = sync;
    invalidateRef.current = () => controller?.abort();
    let unsubscribe = () => {};
    try {
      const auth = firebaseAuth();
      unsubscribe = onAuthStateChanged(
        auth,
        (user) => {
          void sync(user);
        },
        () => {
          if (active)
            setState({
              phase: "error",
              profile: null,
              error:
                "Firebase session state could not be loaded. Try signing in again.",
              user: null,
            });
        },
      );
    } catch (error) {
      setState({
        phase: "error",
        profile: null,
        error: authErrorMessage(error),
        user: null,
      });
    }
    const focus = () => {
      if (userRef.current) void sync(userRef.current, true);
    };
    window.addEventListener("focus", focus);
    return () => {
      active = false;
      controller?.abort();
      invalidateRef.current = () => {};
      unsubscribe();
      window.removeEventListener("focus", focus);
    };
  }, [api]);
  const signIn = useCallback(async (email: string, password: string) => {
    await signInWithEmailAndPassword(firebaseAuth(), email.trim(), password);
  }, []);
  const signUp = useCallback(async (email: string, password: string) => {
    const result = await createUserWithEmailAndPassword(
      firebaseAuth(),
      email.trim(),
      password,
    );
    try {
      await sendEmailVerification(result.user);
      announce("Account created. Check your inbox for the verification email.");
    } catch {
      announce(
        "Account created. Request a verification email below to finish setup.",
      );
    }
  }, []);
  const signOut = useCallback(async () => {
    await firebaseSignOut(firebaseAuth());
    userRef.current = null;
    announce("");
  }, []);
  const verifyEmail = useCallback(async () => {
    const user = userRef.current;
    if (!user) throw new Error("signed_out");
    await sendEmailVerification(user);
    announce("Verification email requested. Check your inbox and spam folder.");
  }, []);
  const refresh = useCallback(async () => {
    const user = userRef.current;
    if (user) {
      await reload(user);
      await user.getIdToken(true);
    }
    await syncRef.current(user, true);
  }, []);
  return (
    <AuthContext.Provider
      value={{
        ...state,
        api,
        signIn,
        signUp,
        signOut,
        verifyEmail,
        refresh,
        announcement,
        announce,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider is required.");
  return value;
}
