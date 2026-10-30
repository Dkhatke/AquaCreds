import type { Metadata } from "next";
import { NotificationsView } from "@/components/console/NotificationsView";

export const metadata: Metadata = { title: "Notifications" };

export default function NotificationsPage() {
  return <NotificationsView />;
}
