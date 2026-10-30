import type { Metadata } from "next";
import { ChainView } from "@/components/chain/ChainView";

export const metadata: Metadata = { title: "Blockchain records" };

export default function ChainPage() {
  return <ChainView />;
}
