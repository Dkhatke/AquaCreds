import type { Metadata } from "next";
import { PageHeader } from "@/components/public/PublicBits";
import { VerifyHashForm } from "@/components/public/VerifyHashForm";

export const metadata: Metadata = { title: "Verify a report hash" };

export default async function VerifyPage({
  searchParams,
}: {
  searchParams: Promise<{ hash?: string }>;
}) {
  const h = (await searchParams).hash;
  const initial = h ?? "";
  return (
    <>
      <PageHeader eyebrow="Public registry" title="Verify a report hash">
        Every approved MRV report has a SHA-256 hash that is anchored on-chain. Paste a hash to see
        whether the registry and the chain know it, and which project and period it belongs to.
      </PageHeader>
      <VerifyHashForm initial={initial.slice(0, 80)} />
    </>
  );
}
