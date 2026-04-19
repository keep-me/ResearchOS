export function validateWorkspaceDirectoryName(value: string): string | null {
  const name = String(value || "").trim();
  if (!name) return "请填写目录名称";
  if (name === "." || name === ".." || name.includes("..")) {
    return "目录名称不能包含 ..";
  }
  if (/[\\/]/.test(name)) {
    return "目录名称不能包含路径分隔符";
  }
  if (/[<>:\"|?*]/.test(name)) {
    return "目录名称包含非法字符";
  }
  return null;
}

export function joinWorkspaceRootPath(rootPath: string, dirName: string): string {
  const root = String(rootPath || "").trim().replace(/[\\/]+$/, "");
  const name = String(dirName || "").trim();
  if (!root) return name;
  if (!name) return root;
  const separator = root.includes("\\") && !root.includes("/") ? "\\" : "/";
  return `${root}${separator}${name}`;
}
