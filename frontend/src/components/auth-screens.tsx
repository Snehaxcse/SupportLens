"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Headphones, ArrowLeft, ShieldCheck } from "lucide-react";
import { roleHome, useAuth } from "@/lib/auth";
import { authErrorMessage } from "@/lib/firebase";
import { useHydrated } from "@/lib/hooks";
import { Brand, Button, Loading, Notice } from "./ui";

export function AuthScreen({ signup = false }: { signup?: boolean }) {
  const auth = useAuth();
  const hydrated = useHydrated();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!hydrated) return;
    if (auth.phase === "ready" && auth.profile)
      router.replace(roleHome(auth.profile.role));
    else if (auth.phase === "unverified") router.replace("/verify-email");
  }, [hydrated, auth.phase, auth.profile, router]);
  return (
    <div className="auth-page">
      <aside className="auth-story">
        <Brand />
        <div>
          <span className="auth-story-icon">
            <Headphones size={38} strokeWidth={1.3} aria-hidden="true" />
          </span>
          <p className="eyebrow">A CLEARER KIND OF SUPPORT</p>
          <h2>
            Every question
            <br />
            deserves a<br />
            <span>conversation.</span>
          </h2>
          <p>
            Your Spotify-focused requests,
            <br />
            all in one thoughtful workspace.
          </p>
        </div>
        <p className="auth-disclosure">
          Independent support. Not affiliated with Spotify.
        </p>
      </aside>
      <main id="main" className="auth-main">
        <Link className="back-link" href="/">
          <ArrowLeft size={16} aria-hidden="true" /> Back to SupportLens
        </Link>
        <div className="auth-form-wrap">
          <span className="auth-mobile-brand">
            <Brand />
          </span>
          <p className="eyebrow">
            {signup ? "A GOOD PLACE TO START" : "WELCOME BACK"}
          </p>
          <h1>
            {signup ? "Let’s get you set up." : "Pick up where you left off."}
          </h1>
          <p className="auth-subtitle">
            {signup
              ? "Create an account to start and follow your support requests."
              : "Sign in to your SupportLens workspace."}
          </p>
          {!hydrated ||
          auth.phase === "checking" ||
          auth.phase === "ready" ||
          auth.phase === "unverified" ? (
            <Loading label="Checking your account…" />
          ) : auth.user ? (
            <SessionProblem />
          ) : (
            <>
              {(error || auth.error) && (
                <Notice
                  kind="error"
                  title={error || auth.error || "Sign-in unavailable"}
                />
              )}
              <form
                className="form-stack"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (busy) return;
                  const data = new FormData(event.currentTarget);
                  setBusy(true);
                  setError("");
                  try {
                    if (signup)
                      await auth.signUp(
                        String(data.get("email")),
                        String(data.get("password")),
                      );
                    else
                      await auth.signIn(
                        String(data.get("email")),
                        String(data.get("password")),
                      );
                  } catch (error) {
                    setError(authErrorMessage(error));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <div className="field">
                  <label htmlFor="email">Email address</label>
                  <input
                    id="email"
                    name="email"
                    type="email"
                    autoComplete="email"
                    required
                    maxLength={320}
                    placeholder="you@example.com"
                    disabled={busy}
                  />
                </div>
                <div className="field">
                  <label htmlFor="password">Password</label>
                  <input
                    id="password"
                    name="password"
                    type="password"
                    autoComplete={signup ? "new-password" : "current-password"}
                    required
                    minLength={signup ? 8 : 1}
                    disabled={busy}
                    aria-describedby={signup ? "password-help" : undefined}
                  />
                  {signup && (
                    <p id="password-help" className="field-help">
                      Use at least 8 characters and meet the project’s password
                      policy.
                    </p>
                  )}
                </div>
                <Button type="submit" disabled={busy}>
                  {busy ? "Connecting…" : signup ? "Create account" : "Sign in"}
                </Button>
              </form>
              <p className="auth-switch">
                {signup ? "Already have an account?" : "New to SupportLens?"}{" "}
                <Link href={signup ? "/sign-in" : "/sign-up"}>
                  {signup ? "Sign in" : "Create an account"}
                </Link>
              </p>
            </>
          )}
          <p className="auth-security">
            <ShieldCheck size={15} aria-hidden="true" /> Secure sign-in with
            Firebase. Access is managed by SupportLens.
          </p>
        </div>
      </main>
    </div>
  );
}
export function SessionProblem() {
  const auth = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function action(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (error) {
      setError(authErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Notice
      title={error || auth.error || "Your account needs attention."}
      kind="error"
      action={
        <div className="inline-actions">
          <Button disabled={busy} onClick={() => void action(auth.refresh)}>
            Refresh account
          </Button>
          <Button
            disabled={busy}
            variant="secondary"
            onClick={() => void action(auth.signOut)}
          >
            Sign out
          </Button>
        </div>
      }
    >
      Access is checked against your active database profile. No ticket
      information is shown until the check succeeds.
    </Notice>
  );
}
export function VerificationScreen() {
  const auth = useAuth();
  const hydrated = useHydrated();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!hydrated) return;
    if (auth.phase === "ready" && auth.profile)
      router.replace(roleHome(auth.profile.role));
    else if (auth.phase === "signed-out") router.replace("/sign-in");
  }, [hydrated, auth.phase, auth.profile, router]);
  async function action(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (error) {
      setError(authErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main id="main" className="verification-page">
      <Brand />
      <div className="verification-card">
        <span className="empty-icon">
          <ShieldCheck size={29} aria-hidden="true" />
        </span>
        <p className="eyebrow">ONE SMALL STEP</p>
        <h1>Verify your email.</h1>
        <p>
          Open the verification email from Firebase, follow its link, then
          return here. Your workspace opens after Firebase and SupportLens
          confirm your account.
        </p>
        {hydrated && auth.announcement && <Notice title={auth.announcement} />}
        {error && <Notice title={error} kind="error" />}
        {!hydrated || auth.phase === "checking" ? (
          <Loading />
        ) : auth.phase === "error" ? (
          <SessionProblem />
        ) : (
          <div className="form-stack">
            <Button disabled={busy} onClick={() => void action(auth.refresh)}>
              {busy ? "Checking…" : "I’ve verified my email"}
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => void action(auth.verifyEmail)}
            >
              Send verification email
            </Button>
            <Button
              variant="quiet"
              disabled={busy}
              onClick={() => void action(auth.signOut)}
            >
              Use another account
            </Button>
          </div>
        )}
        <p className="field-help">
          Check spam if the email hasn’t arrived. Requests may be temporarily
          rate limited.
        </p>
      </div>
    </main>
  );
}
