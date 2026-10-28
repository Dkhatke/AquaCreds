import Link from "next/link";
import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const base =
  "inline-flex items-center justify-center gap-2 rounded-full font-medium transition-colors " +
  "disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";

const variants: Record<ButtonVariant, string> = {
  // Mint = primary action. Deep-ocean text on mint: 8.1:1.
  primary: "bg-lagoon-mint text-deep-ocean hover:bg-[#5cd6b4]",
  secondary: "border border-current text-fg hover:bg-panel-raised",
  ghost: "text-fg hover:bg-panel-raised",
  danger: "border border-failed text-fail hover:bg-failed/10",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-5 text-sm",
  lg: "h-12 px-6 text-base",
};

export function buttonClasses(
  variant: ButtonVariant = "primary",
  size: ButtonSize = "md",
  className?: string,
) {
  return cn(base, variants[variant], sizes[size], className);
}

interface CommonProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

export function Button({
  variant,
  size,
  className,
  type = "button",
  ...rest
}: CommonProps & ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} className={buttonClasses(variant, size, className)} {...rest} />;
}

export function ButtonLink({
  variant,
  size,
  className,
  href,
  ...rest
}: CommonProps & Omit<ComponentProps<typeof Link>, "href" | "children"> & { href: string }) {
  return <Link href={href} className={buttonClasses(variant, size, className)} {...rest} />;
}
