import Link from "next/link";

/** Previous / next links for server-rendered lists; `href(page)` builds the URL. */
export function Pager({
  page,
  pageSize,
  total,
  href,
}: {
  page: number;
  pageSize: number;
  total: number;
  href: (page: number) => string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <nav aria-label="Pagination" className="mt-4 flex items-center justify-between text-sm">
      {page > 1 ? (
        <Link href={href(page - 1)} className="underline underline-offset-2">
          Previous
        </Link>
      ) : (
        <span />
      )}
      <span className="tabular text-fg-muted">
        Page {page} of {pages}
      </span>
      {page < pages ? (
        <Link href={href(page + 1)} className="underline underline-offset-2">
          Next
        </Link>
      ) : (
        <span />
      )}
    </nav>
  );
}

export function pageParam(v: string | undefined): number {
  const n = Number(v);
  return Number.isInteger(n) && n >= 1 ? n : 1;
}
