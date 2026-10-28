import type { ReactNode } from "react";
import { Text, type TextProps, type TextStyle } from "react-native";
import { colors, type } from "../theme";

type Variant = keyof typeof type;

export interface AppTextProps extends TextProps {
  variant?: Variant;
  color?: string;
  children: ReactNode;
}

export function AppText({ variant = "body", color = colors.text, style, ...rest }: AppTextProps) {
  const base: TextStyle = { ...type[variant], color };
  return <Text {...rest} style={[base, style]} maxFontSizeMultiplier={1.6} />;
}
