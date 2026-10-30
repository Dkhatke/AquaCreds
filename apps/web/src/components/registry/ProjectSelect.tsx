"use client";

import { Field, inputClass } from "@/components/registry/fields";
import { useProjects } from "@/lib/queries";

/** Pick one of the projects the user can access. `allLabel` adds an "all projects" option. */
export function ProjectSelect({
  value,
  onChange,
  allLabel,
}: {
  value: string;
  onChange: (id: string) => void;
  allLabel?: string;
}) {
  const { data, isPending, isError } = useProjects();
  return (
    <Field label="Project" className="w-full max-w-sm">
      {({ id }) => (
        <select
          id={id}
          className={inputClass}
          value={value}
          disabled={isPending || isError}
          onChange={(e) => onChange(e.target.value)}
        >
          {allLabel && <option value="">{allLabel}</option>}
          {isPending && <option value="">Loading projects…</option>}
          {isError && <option value="">Projects unavailable</option>}
          {data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}
