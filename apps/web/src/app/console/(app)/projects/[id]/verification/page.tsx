import type { Metadata } from "next";
import { VerificationView } from "@/components/verification/VerificationView";

export const metadata: Metadata = { title: "AI verification" };

export default async function VerificationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <VerificationView projectId={id} />;
}
