import type { Metadata } from "next";
import { ProjectList } from "@/components/console/ProjectList";

export const metadata: Metadata = { title: "MRV reports" };

export default function ReportsIndexPage() {
  return (
    <ProjectList
      title="MRV reports"
      intro="Choose a project to see its monitoring periods, report versions and verifier decisions."
      section="reports"
    />
  );
}
