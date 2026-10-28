"use client";

import { usePathname, useRouter } from "next/navigation";
import { useId } from "react";
import { useProjects } from "@/lib/queries";

/** Choose the project in focus; the URL (`/console/projects/{id}/…`) is the source of truth. */
export function ProjectSwitcher({ currentId }: { currentId: string | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const id = useId();
  const { data, isPending, isError } = useProjects();

  const onChange = (projectId: string) => {
    if (!projectId) return;
    const tail = currentId ? pathname.replace(/^\/console\/projects\/[^/]+/, "") : "/monitoring";
    router.push(`/console/projects/${projectId}${tail || "/monitoring"}`);
  };

  return (
    <div className="flex min-w-0 items-center gap-2">
      <label htmlFor={id} className="hidden text-xs uppercase tracking-wide text-fg-muted sm:block">
        Project
      </label>
      <select
        id={id}
        className="h-9 min-w-0 max-w-[60vw] truncate rounded-lg border border-line bg-mangrove px-2 text-sm text-fg sm:max-w-xs"
        value={currentId ?? ""}
        disabled={isPending || isError}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="" disabled>
          {isPending ? "Loading projects…" : isError ? "Projects unavailable" : "Select a project"}
        </option>
        {data?.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}
