import type { Metadata } from "next";
import { ProjectList } from "@/components/console/ProjectList";

export const metadata: Metadata = { title: "Overview" };

export default function OverviewPage() {
  return (
    <ProjectList
      title="Overview"
      intro="Your projects, their registry status and boundary area. Open a project to see its satellite monitoring, classification, alerts and stewardship score."
    />
  );
}
