import type { components, paths } from "./schema";
export type Schemas = components["schemas"];
export type Profile = Schemas["UserResponse"];
export type Role = Schemas["UserRole"];
export type Status = Schemas["TicketStatus"];
export type Priority = Schemas["Priority"];
export type CustomerTicket = Schemas["CustomerTicket"];
export type AgentTicket = Schemas["AgentTicket"];
export type CustomerDetail = Schemas["CustomerDetail"];
export type AgentDetail = Schemas["AgentDetail"];
export type Agent = Schemas["AssignableAgent"];
export type PublicMessage = Schemas["PublicMessage"];
export type AgentMessage = Schemas["AgentMessage"];
export type PublicEvent = Schemas["PublicEvent"];
export type AgentEvent = Schemas["AgentEvent"];
export type Visibility = Schemas["Visibility"];
export type Page<T> = Omit<Schemas["Page_CustomerTicket_"], "items"> & {
  items: T[];
};
export type InboxQuery = NonNullable<
  paths["/api/v1/agent/tickets"]["get"]["parameters"]["query"]
>;
