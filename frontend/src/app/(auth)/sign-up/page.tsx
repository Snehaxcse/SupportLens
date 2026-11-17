import { AuthScreen } from "@/components/auth-screens";
import { syntheticDemo } from "@/lib/release";
import { redirect } from "next/navigation";
export default function Page() {
  if (syntheticDemo) redirect("/sign-in");
  return <AuthScreen signup />;
}
