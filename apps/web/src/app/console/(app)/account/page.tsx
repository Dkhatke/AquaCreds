import type { Metadata } from "next";
import { AccountView } from "@/components/console/AccountView";

export const metadata: Metadata = { title: "Account security" };

export default function AccountPage() {
  return <AccountView />;
}
