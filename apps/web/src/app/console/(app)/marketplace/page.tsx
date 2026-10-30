import type { Metadata } from "next";
import { MarketConsole } from "@/components/market/MarketConsole";

export const metadata: Metadata = { title: "Carbon marketplace" };

export default function MarketplacePage() {
  return <MarketConsole />;
}
