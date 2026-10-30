"use client";
import { Button, Notice } from "@/components/ui";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main id="main" className="standalone">
      <h1 className="sr-only">Page unavailable</h1>
      <Notice
        title="This page couldn’t load."
        kind="error"
        action={<Button onClick={reset}>Try again</Button>}
      >
        Your last change may have reached the server. Check your tickets before
        repeating a submission.
      </Notice>
    </main>
  );
}
