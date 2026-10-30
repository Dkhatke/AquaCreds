"use client";

import { PublicProviders } from "@/components/public/PublicProviders";
import { Tabs } from "@/components/ui";
import { MarketBrowser } from "./MarketBrowser";
import { TradesPanel } from "./TradesPanel";

export function PublicMarket() {
  return (
    <PublicProviders>
      <Tabs
        label="Marketplace sections"
        items={[
          { id: "listings", label: "Listings", content: <MarketBrowser /> },
          {
            id: "trades",
            label: "Trades and prices",
            content: <TradesPanel allowProjectSelect={false} />,
          },
        ]}
      />
    </PublicProviders>
  );
}
