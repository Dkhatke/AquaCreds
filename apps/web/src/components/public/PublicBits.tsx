import { ErrorState } from "@/components/ui";
import type { Loaded } from "@/lib/public-registry";

export function PageHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="mb-8">
      <p className="text-sm font-medium uppercase tracking-[0.18em] text-mint-ink">{eyebrow}</p>
      <h1 className="mt-2 font-display text-4xl font-semibold leading-tight">{title}</h1>
      {children && <div className="mt-3 max-w-2xl text-fg-muted">{children}</div>}
    </header>
  );
}

/** Show an API failure honestly (the page never substitutes made-up data). */
export function LoadFailed({
  result,
  what,
}: {
  result: Extract<Loaded<unknown>, { ok: false }>;
  what: string;
}) {
  return <ErrorState title={`Could not load ${what}`} error={result.message} />;
}
