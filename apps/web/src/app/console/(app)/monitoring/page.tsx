import type { Metadata } from "next";
import { ProjectList } from "@/components/console/ProjectList";

export const metadata: Metadata = { title: "Satellite monitoring" };

export default function MonitoringIndexPage() {
  return (
    <ProjectList
      title="Satellite monitoring"
      intro="Choose a project to open its map: boundary, ecosystem classification, Sentinel-2 and Sentinel-1 time series, loss alerts and stewardship score."
    />
  );
}
