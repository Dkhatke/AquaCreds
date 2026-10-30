import type { Metadata } from "next";
import { ProjectRegistryList } from "@/components/registry/ProjectRegistryList";

export const metadata: Metadata = { title: "Project registry" };

export default function RegistryPage() {
  return <ProjectRegistryList />;
}
