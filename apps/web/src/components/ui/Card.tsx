import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  as?: "section" | "article" | "div" | "aside";
  title?: ReactNode;
  /** Small line under the title: source, model version, date… */
  meta?: ReactNode;
  actions?: ReactNode;
}

/** Rounded panel with a restrained border; works on both surfaces. */
export function Card({
  as: Tag = "section",
  title,
  meta,
  actions,
  className,
  children,
  ...rest
}: CardProps) {
  return (
    <Tag
      className={cn(
        "rounded-[var(--radius-card)] border border-line bg-panel p-5 shadow-[0_1px_2px_rgb(0_0_0/0.06)]",
        className,
      )}
      {...rest}
    >
      {(title || actions) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="text-base font-semibold leading-tight">{title}</h2>}
            {meta && <p className="mt-1 text-xs text-fg-muted">{meta}</p>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </Tag>
  );
}
