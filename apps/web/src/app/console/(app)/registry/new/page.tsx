import type { Metadata } from "next";
import { NewProjectForm } from "@/components/registry/NewProjectForm";

export const metadata: Metadata = { title: "Register a project" };

export default function NewProjectPage() {
  return <NewProjectForm />;
}
