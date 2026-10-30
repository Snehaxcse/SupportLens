import { ApiError } from "./api/client";
import type { InboxQuery, Page, Status, Priority } from "./api/types";

export const statuses: Status[] = [
  "OPEN",
  "IN_PROGRESS",
  "RESOLVED",
  "REOPENED",
];
export const priorities: Priority[] = ["LOW", "NORMAL", "HIGH"];
export function title(value: string) {
  return value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (character) => character.toUpperCase());
}
export function date(value: string) {
  const time = new Date(value);
  return Number.isNaN(time.getTime())
    ? "Date unavailable"
    : time.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}
export function shortId(id: string) {
  return id.slice(0, 8).toUpperCase();
}
export function pageIndex(value: string | null) {
  const n = Number(value);
  return value &&
    /^\d+$/.test(value) &&
    Number.isInteger(n) &&
    n >= 1 &&
    n <= 10000
    ? n
    : 1;
}
export function isUuid(value: string) {
  return /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(
    value,
  );
}
export function validateText(value: string, max: number) {
  if (!value.trim()) return "Please add some text.";
  if (value.length > max)
    return `Use ${max.toLocaleString()} characters or fewer.`;
  for (const character of value)
    if (character.charCodeAt(0) < 32 && !["\n", "\r", "\t"].includes(character))
      return "Remove unsafe control characters.";
  return "";
}
export async function collectPages<T extends { id: string }>(
  load: (page: number, signal?: AbortSignal) => Promise<Page<T>>,
  signal?: AbortSignal,
): Promise<T[]> {
  const first = await load(1, signal);
  const result = [...first.items];
  if (
    first.page !== 1 ||
    !Number.isInteger(first.total) ||
    first.total < 0 ||
    first.page_size < 1 ||
    first.page_size > 100
  )
    throw new ApiError(502, "queue_changed");
  const pages = Math.ceil(first.total / first.page_size);
  if (pages > 10000) throw new ApiError(422, "queue_changed");
  for (let n = 2; n <= pages; n++) {
    signal?.throwIfAborted();
    const page = await load(n, signal);
    if (
      page.total !== first.total ||
      page.page !== n ||
      page.page_size !== first.page_size ||
      !page.items.length
    )
      throw new ApiError(409, "queue_changed");
    result.push(...page.items);
  }
  if (
    result.length !== first.total ||
    new Set(result.map((item) => item.id)).size !== result.length
  )
    throw new ApiError(409, "queue_changed");
  return result;
}
export function parseInbox(params: URLSearchParams): InboxQuery {
  const status = params.get("status") as Status;
  const priority = params.get("priority") as Priority;
  const assigned = params.get("assigned_agent_id");
  const unassigned = params.get("unassigned_only") === "true";
  const search = params.get("search")?.trim().slice(0, 200);
  return {
    page: pageIndex(params.get("page")),
    page_size: 25,
    order: params.get("order") === "oldest" ? "oldest" : "newest",
    ...(statuses.includes(status) ? { status } : {}),
    ...(priorities.includes(priority) ? { priority } : {}),
    ...(unassigned
      ? { unassigned_only: true }
      : assigned && isUuid(assigned)
        ? { assigned_agent_id: assigned }
        : {}),
    ...(search ? { search } : {}),
  };
}
export const transitions: Record<Status, Status[]> = {
  OPEN: ["IN_PROGRESS", "RESOLVED"],
  IN_PROGRESS: ["RESOLVED"],
  RESOLVED: ["REOPENED"],
  REOPENED: ["IN_PROGRESS", "RESOLVED"],
};
