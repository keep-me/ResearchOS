export const VISUAL_STYLE_STORAGE_KEY = "researchos.visual-style";

export const visualStyles = [
  {
    id: "notion-style",
    label: "Notion",
    shortLabel: "DOC",
    caption: "清晰文档",
    preview: ["#f0efeb", "#ffffff", "#1769d1"],
    previewInk: "#24211d",
  },
  {
    id: "cel-shading",
    label: "Cel Shading",
    shortLabel: "CEL",
    caption: "赛璐璐",
    preview: ["#fafaf5", "#e63946", "#4ea8de"],
    previewInk: "#1a1a2e",
  },
  {
    id: "korean-minimal",
    label: "K-Minimal",
    shortLabel: "AIR",
    caption: "韩式极简",
    preview: ["#faf9f7", "#d4a5a5", "#a8c5b8"],
    previewInk: "#3d4a5c",
  },
  {
    id: "linear-style",
    label: "Linear",
    shortLabel: "LIN",
    caption: "精密暗色",
    preview: ["#08090a", "#191a1b", "#7170ff"],
    previewInk: "#f7f8f8",
  },
] as const;

export type VisualStyle = (typeof visualStyles)[number]["id"];

export const DEFAULT_VISUAL_STYLE: VisualStyle = "notion-style";

const visualStyleIds = new Set<string>(visualStyles.map((style) => style.id));

export function isVisualStyle(value: string | null | undefined): value is VisualStyle {
  return Boolean(value && visualStyleIds.has(value));
}

export function getNextVisualStyle(current: VisualStyle): VisualStyle {
  const currentIndex = visualStyles.findIndex((style) => style.id === current);
  const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % visualStyles.length : 0;
  return visualStyles[nextIndex].id;
}
