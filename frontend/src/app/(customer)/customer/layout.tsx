import type { ReactNode } from "react";
import { Workspace } from "@/components/shell";
export default function Layout({ children }: { children: ReactNode }) {
  return <Workspace role="CUSTOMER">{children}</Workspace>;
}
