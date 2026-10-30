import { Brand, ButtonLink, Empty } from "@/components/ui";
export default function NotFound() {
  return (
    <main id="main" className="standalone">
      <Brand />
      <h1 className="sr-only">Page not found</h1>
      <Empty
        title="This page is out of tune."
        action={<ButtonLink href="/">Back to SupportLens</ButtonLink>}
      >
        Check the address or return to the home page.
      </Empty>
    </main>
  );
}
