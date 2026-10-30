"use client";

import { Tabs } from "@/components/ui";
import { ForwardFunding } from "./ForwardFunding";
import { MarketBrowser } from "./MarketBrowser";
import { TradesPanel } from "./TradesPanel";

export function MarketNotice() {
  return (
    <p className="max-w-2xl text-sm text-fg-muted">
      Payments use a mock stablecoin on the Polygon Amoy testnet, so every trade is a demonstration
      transaction. Credit units are estimated, pending accredited verification; testnet registry
      units.
    </p>
  );
}

export function MarketConsole() {
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-8">
      <h1 className="font-display text-3xl font-semibold">Carbon marketplace</h1>
      <MarketNotice />
      <Tabs
        label="Marketplace sections"
        items={[
          { id: "listings", label: "Listings", content: <MarketBrowser /> },
          { id: "trades", label: "Trades and prices", content: <TradesPanel /> },
          { id: "forward", label: "Forward funding", content: <ForwardFunding /> },
        ]}
      />
    </div>
  );
}
