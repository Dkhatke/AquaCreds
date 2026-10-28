import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface Column<Row> {
  key: string;
  header: ReactNode;
  cell: (row: Row) => ReactNode;
  /** Right-align numeric columns (tabular numerals are applied to every cell). */
  numeric?: boolean;
}

interface DataTableProps<Row> {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  caption: ReactNode;
  /** Visually hide the caption (it stays available to screen readers). */
  hideCaption?: boolean;
  empty?: ReactNode;
  className?: string;
}

/** Calm, dense table for data pages. */
export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  caption,
  hideCaption,
  empty,
  className,
}: DataTableProps<Row>) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full border-collapse text-sm">
        <caption className={cn("mb-2 text-left text-xs text-fg-muted", hideCaption && "sr-only")}>
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-line">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cn(
                  "px-2 py-2 text-xs font-medium uppercase tracking-wide text-fg-muted",
                  c.numeric ? "text-right" : "text-left",
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular">
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-2 py-4 text-fg-muted">
                {empty ?? "No rows."}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={rowKey(row)} className="border-b border-line/60 last:border-0">
                {columns.map((c) => (
                  <td key={c.key} className={cn("px-2 py-2 align-top", c.numeric && "text-right")}>
                    {c.cell(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
