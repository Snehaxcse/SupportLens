import { Brand } from "@/components/ui";
import { SessionProblem } from "@/components/auth-screens";
export default function Page() {
  return (
    <main id="main" className="standalone">
      <Brand />
      <h1 className="mt-8">Account access</h1>
      <div className="mt-8">
        <SessionProblem />
      </div>
    </main>
  );
}
