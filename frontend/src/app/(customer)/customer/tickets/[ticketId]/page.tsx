import { CustomerTicketScreen } from "@/components/customer";
export default async function Page({
  params,
}: {
  params: Promise<{ ticketId: string }>;
}) {
  const { ticketId } = await params;
  return <CustomerTicketScreen id={ticketId} />;
}
