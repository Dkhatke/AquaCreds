import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { ConsoleShell } from "@/components/console/ConsoleShell";
import { COOKIES } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function ConsoleAppLayout({ children }: { children: ReactNode }) {
  const demo = (await cookies()).get(COOKIES.demo)?.value;
  return (
    <ConsoleShell demo={Boolean(demo)} banner={demo && demo !== "1" ? demo : null}>
      {children}
    </ConsoleShell>
  );
}
