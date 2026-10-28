/** Shown on every console page while a demo session is active (SIH requirement: demo data is
 *  visibly marked). The text comes from the API's `banner` when available. */
export function DemoBanner({ text }: { text?: string | null | undefined }) {
  return (
    <div
      role="note"
      aria-label="Demo mode"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-review-amber/50 bg-review-amber px-4 py-2 text-sm text-deep-ocean"
    >
      <strong className="rounded bg-deep-ocean px-1.5 py-0.5 text-xs uppercase tracking-wider text-review-amber">
        Demo
      </strong>
      <span>
        {text ||
          "You are exploring synthetic demo data. Nothing here is a real project, a real measurement or a real credit; transactions are demonstration transactions."}
      </span>
    </div>
  );
}
