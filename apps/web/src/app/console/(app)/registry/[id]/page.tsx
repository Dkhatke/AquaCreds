import type { Metadata } from "next";
import { ProjectDetail } from "@/components/registry/ProjectDetail";

export const metadata: Metadata = { title: "Project" };

export default async function RegistryProjectPage({ params }: { params: Promise<{ id: string }> }) {
  return <ProjectDetail projectId={(await params).id} />;
}
