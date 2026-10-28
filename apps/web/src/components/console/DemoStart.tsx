"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, ErrorState, LoadingState } from "@/components/ui";
import { errorMessage, postBff } from "@/lib/api-browser";

/** Starts a demo session (no account) and opens the first demo project's monitoring page. */
export function DemoStart() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  const start = useCallback(async () => {
    setError(null);
    try {
      const res = await postBff<{ ok: true; project_ids: string[] }>("/bff/auth/demo");
      const first = res.project_ids[0];
      router.replace(first ? `/console/projects/${first}/monitoring` : "/console");
      router.refresh();
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [router]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void start();
  }, [start]);

  if (error) {
    return (
      <div className="space-y-3">
        <ErrorState title="The demo session could not be started" error={error} />
        <Button onClick={() => void start()}>Try again</Button>
      </div>
    );
  }
  return <LoadingState label="Opening the demo project" />;
}
