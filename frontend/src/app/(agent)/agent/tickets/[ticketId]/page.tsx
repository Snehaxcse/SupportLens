import { AgentTicketScreen } from "@/components/agent";
export default async function Page({
  params,
}: {
  params: Promise<{ ticketId: string }>;
}) {
  const { ticketId } = await params;
  return <AgentTicketScreen id={ticketId} />;
}
