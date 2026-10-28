/** Only follow `?next=` to console pages on this site (no open redirects). */
export function safeNext(next: string | null | undefined): string {
  if (!next?.startsWith("/console") || next.startsWith("//") || next.includes("\\")) {
    return "/console";
  }
  return next.startsWith("/console/login") ? "/console" : next;
}
