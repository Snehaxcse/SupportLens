import { getApps, initializeApp } from "@firebase/app";
import {
  browserSessionPersistence,
  getAuth,
  initializeAuth,
  type Auth,
} from "@firebase/auth";
import { ApiError } from "./api/client";

let cached: Auth | undefined;
export function firebaseAuth(): Auth {
  if (cached) return cached;
  const apiKey = process.env.NEXT_PUBLIC_FIREBASE_API_KEY;
  const projectId = process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
  if (
    !apiKey ||
    !projectId ||
    apiKey.startsWith("REPLACE_") ||
    projectId.startsWith("REPLACE_")
  )
    throw new ApiError(0, "configuration");
  const existing = getApps().find((app) => app.name === "supportlens-web");
  if (existing) cached = getAuth(existing);
  else {
    const app = initializeApp(
      {
        apiKey,
        projectId,
        ...(process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
          ? { authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN }
          : {}),
        ...(process.env.NEXT_PUBLIC_FIREBASE_APP_ID
          ? { appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID }
          : {}),
      },
      "supportlens-web",
    );
    cached = initializeAuth(app, { persistence: browserSessionPersistence });
  }
  return cached;
}
export function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  const code =
    typeof error === "object" && error !== null && "code" in error
      ? String(error.code)
      : "";
  if (
    [
      "auth/invalid-credential",
      "auth/wrong-password",
      "auth/user-not-found",
      "auth/user-disabled",
    ].includes(code)
  )
    return "Sign-in was not accepted. Check your email and password, or contact the administrator.";
  if (code === "auth/too-many-requests")
    return "Too many attempts. Please wait before trying again.";
  if (code === "auth/network-request-failed")
    return "Firebase could not be reached. Check your connection and try again.";
  if (
    code === "auth/weak-password" ||
    code === "auth/password-does-not-meet-requirements"
  )
    return "Choose a stronger password that meets the Firebase project password policy.";
  if (code === "auth/email-already-in-use")
    return "An account could not be created with these details. Try signing in instead.";
  return "Authentication could not be completed. Check your details and try again.";
}
