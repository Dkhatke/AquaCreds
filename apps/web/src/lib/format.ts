const nf = (digits: number) =>
  new Intl.NumberFormat("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Format a number with a fixed number of decimals (Indian digit grouping). */
export function formatNumber(value: number, digits = 0): string {
  return nf(digits).format(value);
}

/** 0.9451 → "94.5 %" */
export function formatPercent(fraction: number, digits = 1): string {
  return `${nf(digits).format(fraction * 100)} %`;
}

const dateFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** ISO date or date-time → "14 Dec 2024" (UTC, so server and browser agree). */
export function formatDate(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? iso : dateFmt.format(d);
}

/** Shorten a hex hash for display: "3f9a…c21e". The full value goes in a title/copy affordance. */
export function shortHash(hash: string, head = 6, tail = 4): string {
  return hash.length <= head + tail + 1 ? hash : `${hash.slice(0, head)}…${hash.slice(-tail)}`;
}

/** snake_case / kebab-case identifier → "Sentence case". */
export function humanize(id: string): string {
  const words = id.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
