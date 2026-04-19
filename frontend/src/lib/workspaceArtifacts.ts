export type ArtifactPreviewState = {
  title: string;
  path: string;
  serverId: string;
  content: string;
  truncated: boolean;
  markdown: boolean;
};

export interface ArtifactReadCandidate {
  workspacePath: string;
  relativePath: string;
}

const TEXT_PREVIEW_EXTENSIONS = new Set([
  "md", "markdown", "txt", "log", "json", "yaml", "yml", "toml", "ini", "cfg",
  "py", "ps1", "sh", "ts", "tsx", "js", "jsx", "css", "html", "xml", "csv",
]);

export function normalizeServerId(value: string | null | undefined): string {
  return (value || "").trim() || "local";
}

export function normalizePathValue(value: string | null | undefined): string {
  return (value || "").replace(/\\/g, "/").replace(/\/+$/, "").trim();
}

export function fileNameFromPath(value: string | null | undefined): string {
  const normalized = normalizePathValue(value);
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || normalized;
}

export function fileExtension(value: string | null | undefined): string {
  const fileName = fileNameFromPath(value);
  const index = fileName.lastIndexOf(".");
  return index >= 0 ? fileName.slice(index + 1).toLowerCase() : "";
}

export function isPreviewableArtifact(path: string | null | undefined): boolean {
  return TEXT_PREVIEW_EXTENSIONS.has(fileExtension(path));
}

export function isMarkdownArtifact(path: string | null | undefined): boolean {
  const ext = fileExtension(path);
  return ext === "md" || ext === "markdown";
}

export function deriveRelativePath(
  workspacePath: string | null | undefined,
  filePath: string | null | undefined,
): string {
  const workspace = normalizePathValue(workspacePath);
  const target = normalizePathValue(filePath);
  if (!workspace || !target) return "";
  if (target === workspace) return "";
  if (target.startsWith(`${workspace}/`)) {
    return target.slice(workspace.length + 1);
  }
  return "";
}

function normalizeRelativePathValue(value: string | null | undefined): string {
  return (value || "").replace(/\\/g, "/").replace(/^\/+/, "").trim();
}

export function buildArtifactReadCandidates(
  roots: Array<string | null | undefined>,
  artifact: Pick<{ path?: string | null; relative_path?: string | null }, "path" | "relative_path">,
): ArtifactReadCandidate[] {
  const candidates: ArtifactReadCandidate[] = [];
  const seen = new Set<string>();
  const normalizedRoots = roots.map((item) => normalizePathValue(item)).filter(Boolean);
  const explicitRelativePath = normalizeRelativePathValue(artifact.relative_path);
  const artifactPath = normalizePathValue(artifact.path);

  const pushCandidate = (workspacePath: string, relativePath: string) => {
    const normalizedWorkspace = normalizePathValue(workspacePath);
    const normalizedRelative = normalizeRelativePathValue(relativePath);
    if (!normalizedWorkspace || !normalizedRelative) return;
    const key = `${normalizedWorkspace}::${normalizedRelative}`;
    if (seen.has(key)) return;
    seen.add(key);
    candidates.push({
      workspacePath: normalizedWorkspace,
      relativePath: normalizedRelative,
    });
  };

  for (const root of normalizedRoots) {
    if (explicitRelativePath) {
      pushCandidate(root, explicitRelativePath);
    }
    const derivedRelativePath = deriveRelativePath(root, artifactPath);
    if (derivedRelativePath) {
      pushCandidate(root, derivedRelativePath);
    }
  }

  return candidates;
}
