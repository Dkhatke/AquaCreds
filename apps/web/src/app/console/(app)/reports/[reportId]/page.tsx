import type { Metadata } from "next";
import { cookies } from "next/headers";
import { ReportDetailView } from "@/components/reports/ReportDetailView";
import { COOKIES } from "@/lib/session";

export const metadata: Metadata = { title: "MRV report" };

export default async function ReportPage({ params }: { params: Promise<{ reportId: string }> }) {
  const { reportId } = await params;
  const demo = Boolean((await cookies()).get(COOKIES.demo)?.value);
  return <ReportDetailView reportId={reportId} demo={demo} />;
}
