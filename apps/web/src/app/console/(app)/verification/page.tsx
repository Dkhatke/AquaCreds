import type { Metadata } from "next";
import { ProjectList } from "@/components/console/ProjectList";

export const metadata: Metadata = { title: "AI verification" };

export default function VerificationIndexPage() {
  return (
    <ProjectList
      title="AI verification"
      intro="Choose a project to review the automated checks on its field photos."
      section="verification"
    />
  );
}
