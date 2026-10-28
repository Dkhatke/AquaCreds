import type { Metadata } from "next";
import { MonitoringView } from "@/components/monitoring/MonitoringView";
import { featureFlags } from "@/lib/config";
import { imagery, mapOverlay } from "@/lib/imagery";

export const metadata: Metadata = { title: "Satellite monitoring" };

export default async function MonitoringPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <MonitoringView
      projectId={id}
      tilesEnabled={featureFlags().tiles}
      overlay={{ ...mapOverlay, attribution: imagery.attribution }}
    />
  );
}
