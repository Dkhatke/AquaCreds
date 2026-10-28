import type { Metadata } from "next";
import Link from "next/link";
import { Logo } from "@/components/brand/Logo";
import { LoginForm } from "@/components/console/LoginForm";
import { featureFlags } from "@/lib/config";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[]; expired?: string | string[] }>;
}) {
  const sp = await searchParams;
  const next = typeof sp.next === "string" ? sp.next : null;
  const { demo } = featureFlags();
  return (
    <main id="main" className="grid min-h-dvh place-items-center px-5 py-12">
      <div className="w-full max-w-sm">
        <Link href="/" aria-label="AquaCreds home">
          <Logo />
        </Link>
        <h1 className="mt-10 font-display text-3xl font-semibold">Sign in to the console</h1>
        <p className="mt-2 text-sm text-fg-muted">
          For project organisations, verifiers and registry staff.
        </p>
        <div className="mt-8">
          <LoginForm next={next} expired={sp.expired === "1"} />
        </div>
        {demo && (
          <p className="mt-8 text-sm text-fg-muted">
            No account?{" "}
            <Link
              href="/console/demo"
              className="font-medium text-lagoon-mint underline underline-offset-2"
            >
              Explore the demo
            </Link>{" "}
            on synthetic data.
          </p>
        )}
      </div>
    </main>
  );
}
