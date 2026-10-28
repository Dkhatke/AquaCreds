"use client";

import { type KeyboardEvent, type ReactNode, useId, useRef, useState } from "react";
import { cn } from "@/lib/cn";

export interface TabItem {
  id: string;
  label: ReactNode;
  content: ReactNode;
}

/** WAI-ARIA tabs: arrow keys / Home / End move between tabs; only the active tab is tabbable. */
export function Tabs({
  items,
  label,
  defaultTab,
  onChange,
  className,
}: {
  items: TabItem[];
  label: string;
  defaultTab?: string;
  onChange?: (id: string) => void;
  className?: string;
}) {
  const [active, setActive] = useState(defaultTab ?? items[0]?.id ?? "");
  const base = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const select = (id: string, focus = false) => {
    setActive(id);
    onChange?.(id);
    if (focus) refs.current[id]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const idx = items.findIndex((t) => t.id === active);
    const go = (i: number) => {
      const next = items[(i + items.length) % items.length];
      if (next) select(next.id, true);
    };
    if (e.key === "ArrowRight") go(idx + 1);
    else if (e.key === "ArrowLeft") go(idx - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(items.length - 1);
    else return;
    e.preventDefault();
  };

  return (
    <div className={className}>
      <div role="tablist" aria-label={label} className="flex gap-1 border-b border-line">
        {items.map((t) => {
          const selected = t.id === active;
          return (
            <button
              key={t.id}
              ref={(el) => {
                refs.current[t.id] = el;
              }}
              type="button"
              role="tab"
              id={`${base}-tab-${t.id}`}
              aria-selected={selected}
              aria-controls={`${base}-panel-${t.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => select(t.id)}
              onKeyDown={onKeyDown}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
                selected
                  ? "border-lagoon-mint text-fg"
                  : "border-transparent text-fg-muted hover:text-fg",
              )}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      {items.map((t) => (
        <div
          key={t.id}
          role="tabpanel"
          id={`${base}-panel-${t.id}`}
          aria-labelledby={`${base}-tab-${t.id}`}
          hidden={t.id !== active}
          className="pt-4"
        >
          {t.id === active && t.content}
        </div>
      ))}
    </div>
  );
}
