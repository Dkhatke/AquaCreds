import type { Metadata } from "next";
import { CommunityView } from "@/components/community/CommunityView";

export const metadata: Metadata = { title: "Community portal" };

export default function CommunityPage() {
  return <CommunityView />;
}
