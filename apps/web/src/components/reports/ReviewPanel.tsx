"use client";

import { unwrap } from "@aquacreds/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Button, Card, ErrorState } from "@/components/ui";
import { browserClient, errorMessage } from "@/lib/api-browser";
import { DECISIONS, type ReviewDecision, reviewError } from "@/lib/mrv";
import { keys } from "@/lib/queries";

/** Verifier decision form. The API enforces independence; its 403 message is shown as is. */
export function ReviewPanel({
  reportId,
  projectId,
  periodId,
  belongsToOrg,
}: {
  reportId: string;
  projectId: string;
  periodId: string;
  belongsToOrg: boolean;
}) {
  const qc = useQueryClient();
  const uid = useId();
  const [decision, setDecision] = useState<ReviewDecision>("approve");
  const [comment, setComment] = useState("");
  const [touched, setTouched] = useState(false);
  const problem = reviewError(decision, comment);

  const review = useMutation({
    mutationFn: () =>
      unwrap(
        browserClient().api.POST("/api/v1/reports/{report_id}/review", {
          params: { path: { report_id: reportId } },
          body: { decision, comment },
        }),
      ),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: keys.report(reportId) }),
        qc.invalidateQueries({ queryKey: keys.reports(periodId) }),
        qc.invalidateQueries({ queryKey: keys.periods(projectId) }),
        qc.invalidateQueries({ queryKey: keys.project(projectId) }),
      ]);
    },
  });

  return (
    <Card title="Verifier decision" meta="Recorded with your name, time and the report hash">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          if (!problem) review.mutate();
        }}
      >
        {belongsToOrg && (
          <p role="note" className="text-sm text-warn">
            You belong to this project's organisation. The API will refuse your decision (conflict
            of interest).
          </p>
        )}
        <fieldset>
          <legend className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Decision
          </legend>
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
            {DECISIONS.map((d) => (
              <label key={d.id} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name={`${uid}-decision`}
                  checked={decision === d.id}
                  onChange={() => setDecision(d.id)}
                />
                {d.label}
              </label>
            ))}
          </div>
        </fieldset>
        <div>
          <label htmlFor={`${uid}-comment`} className="text-sm text-fg-muted">
            Comment {decision === "approve" ? "(optional)" : "(required)"}
          </label>
          <textarea
            id={`${uid}-comment`}
            rows={3}
            maxLength={5000}
            value={comment}
            aria-invalid={touched && problem ? true : undefined}
            aria-describedby={touched && problem ? `${uid}-err` : undefined}
            onChange={(e) => setComment(e.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-panel p-2 text-sm"
          />
          {touched && problem && (
            <p id={`${uid}-err`} role="alert" className="mt-1 text-sm text-fail">
              {problem}
            </p>
          )}
        </div>
        {review.isError && (
          <ErrorState title="Decision not recorded" error={errorMessage(review.error)} />
        )}
        <Button
          type="submit"
          variant={decision === "reject" ? "danger" : "primary"}
          disabled={review.isPending}
        >
          {review.isPending ? "Saving…" : DECISIONS.find((d) => d.id === decision)?.label}
        </Button>
      </form>
    </Card>
  );
}
