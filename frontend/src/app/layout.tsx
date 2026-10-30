import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Suspense } from "react";
import "./globals.css";
import "./workspace.css";
import { AuthProvider } from "@/lib/auth";

export const metadata: Metadata = {
  title: {
    default: "SupportLens — A little clarity. Back to listening.",
    template: "%s · SupportLens",
  },
  description:
    "An independent support workspace for Spotify-focused questions. Submit a request, keep the conversation together, and follow its progress.",
  icons: { icon: "/icon.svg" },
};
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <AuthProvider>
          <Suspense>{children}</Suspense>
        </AuthProvider>
      </body>
    </html>
  );
}
