"use client";

import { useId } from "react";
import { Button } from "@/components/ui";

/**
 * Buying, listing and retiring credits are signed by the buyer's or seller's own wallet. Wallet
 * connection is not built yet, so these actions are shown as unavailable; AquaCreds never signs
 * them on a user's behalf and never fakes a transaction.
 */
export function WalletGate({ action }: { action: string }) {
  const noteId = useId();
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button variant="secondary" size="sm" disabled aria-describedby={noteId}>
        Connect wallet (coming soon)
      </Button>
      <p id={noteId} className="text-xs text-fg-muted">
        {action} needs your own wallet. No transaction has been or will be made on your behalf.
      </p>
    </div>
  );
}
