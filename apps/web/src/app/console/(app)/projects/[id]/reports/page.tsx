import type { Metadata } from "next";
import { cookies } from "next/headers";
import { ProjectReportsView } from "@/components/reports/ProjectReportsView";
import { COOKIES } from "@/lib/session";

export const metadata: Metadata = { title: "MRV reports" };

export default async function ProjectReportsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const demo = Boolean((await cookies()).get(COOKIES.demo)?.value);
  return <ProjectReportsView projectId={id} demo={demo} />;
}
