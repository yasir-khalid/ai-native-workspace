/* PodOS generative-UI library: a curated subset of OpenUI's built-in components (see spec.json). */
// From the package root (not /genui-lib): that entry bundles its own theme context, so charts would ignore our ThemeProvider.
import { createElement } from "react";
import type { ComponentRenderProps } from "@openuidev/react-lang";
import { openuiLibrary, BarChartCondensed } from "@openuidev/react-ui";
import spec from "./spec.json";

const keep = new Set<string>(spec.keep);

type SeriesNode = { type?: string; props?: Record<string, unknown> };

/** Mirrors OpenUI's chart-data builder for the `labels` + `Series()` shape used in our prompt. */
function barData(labels: unknown, series: unknown) {
  if (!Array.isArray(labels) || !Array.isArray(series)) return [];
  const nodes = series.filter((s): s is SeriesNode => typeof s === "object" && s !== null);
  return labels.map((label, i) => {
    const point: Record<string, string | number> = { category: String(label) };
    for (const node of nodes) {
      const category = node.props?.category;
      const values = node.props?.values;
      if (typeof category === "string" && Array.isArray(values)) point[category] = Number(values[i]) || 0;
    }
    return point;
  });
}

// OpenUI's generated BarChart hard-codes BarChartCondensed, which drops every unknown prop and caps bars
// at 16px. Rebuild the same chart here so wide cards get readable bar widths without changing the schema.
const barChart = openuiLibrary.components.BarChart;
const widerBarChart = {
  ...barChart,
  component: ({ props }: ComponentRenderProps<Record<string, unknown>>) => {
    const data = barData(props.labels, props.series);
    if (!data.length) return null;
    return createElement(BarChartCondensed, {
      data,
      categoryKey: "category",
      variant: props.variant as "grouped" | "stacked" | undefined,
      xAxisLabel: props.xLabel as string | undefined,
      yAxisLabel: props.yLabel as string | undefined,
      height: props.height as number | undefined,
      barRadius: 6,
      maxBarWidth: 40,
      isAnimationActive: false,
      className: "podos-bar-chart",
    });
  },
};

export const podosLibrary = openuiLibrary.extend({
  components: {
    remove: Object.keys(openuiLibrary.components).filter((name) => !keep.has(name)),
    override: [widerBarChart],
  },
});

/** Fenced-code languages that carry OpenUI Lang in an assistant message. */
export const OPENUI_LANGS = new Set(["openui-lang", "openui"]);
