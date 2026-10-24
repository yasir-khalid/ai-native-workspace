"use client";

/* Renders an ```openui-lang block from an agent answer as live UI (OpenUI by Thesys: openui.com).
 *
 * Safety: the library is a curated subset (lib/openui/spec.json) with no images, links or forms, no
 * toolProvider is passed (so Query()/Mutation() can't reach anything), and the only action honoured
 * is "ask a follow-up" — @OpenUrl and custom actions are ignored. */

import { useCallback, useState } from "react";
import { BuiltinActionType, Renderer, type ActionEvent, type OpenUIError } from "@openuidev/react-lang";
import { createTheme, defaultLightTheme, ThemeProvider } from "@openuidev/react-ui";
import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";
import { useAui, useAuiState } from "@assistant-ui/react";
import { podosLibrary } from "@/lib/openui/library";

const FONT = "var(--font-ui-sans), ui-sans-serif, system-ui, sans-serif";

// OpenUI's composite type tokens (e.g. textHeadingSm = "600 18px/1.25 \"Inter\", sans-serif") embed their own
// family, so every one of them is rewritten to the app font.
const typeTokens = Object.fromEntries(
  Object.entries(defaultLightTheme)
    .filter(([k, v]) => /^text(Body|Heading|Label|Code|Number)/.test(k) && typeof v === "string" && v.includes("Inter"))
    .map(([k, v]) => [k, (v as string).replace(/"Inter", sans-serif/, FONT)]),
);

// Linear chart colours. OpenUI takes single-series charts from the MIDDLE of the palette, so indigo sits at
// the centre; two series get blue + green, three get blue / indigo / green.
const CHART = ["#eb5757", "#4cb782", "#4ea7fc", "#5e6ad2", "#f2c94c", "#00b8cc", "#d6409f"];
const palettes = Object.fromEntries(
  ["defaultChartPalette", "barChartPalette", "lineChartPalette", "areaChartPalette", "pieChartPalette", "radarChartPalette",
    "radialChartPalette", "horizontalBarChartPalette"].map((k) => [k, CHART]),
);

const podosTheme = createTheme({
  // Light palette: near-black ink, 8% black hairlines, Linear indigo actions.
  ...typeTokens,
  ...palettes,
  fontBody: FONT,
  fontHeading: FONT,
  fontLabel: FONT,
  fontNumbers: FONT,
  fontCode: "var(--font-geist-mono), ui-monospace, monospace",
  background: "#ffffff",
  popoverBackground: "#ffffff",
  textNeutralPrimary: "#0d0d0d",
  textNeutralSecondary: "#6e6e80",
  interactiveAccentDefault: "#0d0d0d",
  interactiveAccentHover: "#262626",
  interactiveAccentPressed: "#000000",
  borderDefault: "#00000014",
  borderInteractive: "#00000024",
});

export function OpenUIBlock({ code }: SyntaxHighlighterProps) {
  const aui = useAui();
  const running = useAuiState((s) => s.message.status?.type === "running");
  const [errors, setErrors] = useState<OpenUIError[]>([]);

  const onAction = useCallback(
    (event: ActionEvent) => {
      if (event.type !== BuiltinActionType.ContinueConversation) return; // no navigation, no custom actions
      const text = (event.humanFriendlyMessage ?? "").trim().slice(0, 500);
      if (!text || aui.thread.getState().isRunning) return;
      aui.thread.append(text); // a new user turn at the end of the thread (this block lives inside a message)
    },
    [aui],
  );

  return (
    <div
      className="podos-genui my-4 rounded-lg border bg-card p-4"
      data-testid="openui-block"
    >
      <ThemeProvider mode="light" lightTheme={podosTheme}>
        <Renderer response={code} library={podosLibrary} isStreaming={running} onAction={onAction} onError={setErrors} />
      </ThemeProvider>
      {!running && errors.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">Part of this visualization couldn&apos;t be drawn.</p>
      )}
    </div>
  );
}

export const openUIByLanguage = {
  "openui-lang": { CodeHeader: () => null, SyntaxHighlighter: OpenUIBlock },
  openui: { CodeHeader: () => null, SyntaxHighlighter: OpenUIBlock },
};
