import { cn } from "@/lib/cn";

/** Wordmark with a mark drawn from mangrove prop roots meeting the waterline. */
export function Logo({ className, withText = true }: { className?: string; withText?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true" focusable="false">
        <rect width="32" height="32" rx="8" fill="#103A38" />
        <path
          d="M16 6v9M16 15c-3 0-5.5 2.5-6.5 7M16 15c3 0 5.5 2.5 6.5 7M16 15v7"
          stroke="#43C9A4"
          strokeWidth="2"
          strokeLinecap="round"
          fill="none"
        />
        <path
          d="M6 23.5c2.5-1.6 5-1.6 7.5 0s5 1.6 7.5 0 3.5-1.2 5 0"
          stroke="#E9F0EC"
          strokeWidth="1.6"
          strokeLinecap="round"
          fill="none"
        />
      </svg>
      {withText && (
        <span className="font-display text-lg font-semibold tracking-tight">AquaCreds</span>
      )}
    </span>
  );
}
