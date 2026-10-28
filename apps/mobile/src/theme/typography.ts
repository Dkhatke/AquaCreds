import type { TextStyle } from "react-native";

/** Font family names registered with expo-font in app/_layout.tsx (bundled, no runtime fetch). */
export const fonts = {
  display: "SpaceGrotesk_600SemiBold",
  body: "Inter_400Regular",
  bodyMedium: "Inter_500Medium",
  bodyBold: "Inter_600SemiBold",
} as const;

/** Data values (coordinates, accuracy, scores, hashes) use tabular numerals so columns line up. */
export const tabular: TextStyle = { fontVariant: ["tabular-nums"] };

export const type = {
  title: { fontFamily: fonts.display, fontSize: 28, lineHeight: 34 },
  heading: { fontFamily: fonts.display, fontSize: 20, lineHeight: 26 },
  body: { fontFamily: fonts.body, fontSize: 17, lineHeight: 24 },
  label: { fontFamily: fonts.bodyMedium, fontSize: 15, lineHeight: 20 },
  button: { fontFamily: fonts.bodyBold, fontSize: 18, lineHeight: 22 },
  data: { fontFamily: fonts.bodyMedium, fontSize: 17, lineHeight: 22, ...tabular },
  dataLarge: { fontFamily: fonts.bodyBold, fontSize: 24, lineHeight: 30, ...tabular },
  mono: { fontFamily: fonts.body, fontSize: 13, lineHeight: 18, ...tabular },
} satisfies Record<string, TextStyle>;
