import type { LucideIcon } from "@/lib/lucide";
import {
  BookCopy,
  FileStack,
  FolderKanban,
  ListTodo,
  MessageSquare,
  Network,
  Newspaper,
  PenTool,
  Search,
  Settings,
} from "@/lib/lucide";

export type ShellNavGroup = "research-input" | "research-analysis" | "execution" | "system";

export interface ShellNavItem {
  to: string;
  label: string;
  description: string;
  icon: LucideIcon;
  group: ShellNavGroup;
}

export interface ShellNavSection {
  key: string;
  title: string;
  caption: string;
  items: ShellNavItem[];
}

export interface RouteMeta {
  title: string;
  subtitle: string;
  eyebrow: string;
}

export interface ShellNavMatch {
  section: ShellNavSection;
  item: ShellNavItem;
}

export const shellNavSections: ShellNavSection[] = [
  {
    key: "execution-primary",
    title: "执行协作",
    caption: "执行协作",
    items: [
      {
        to: "/assistant",
        label: "研究助手",
        description: "研究助手主入口",
        icon: MessageSquare,
        group: "execution",
      },
    ],
  },
  {
    key: "research-input",
    title: "研究输入",
    caption: "研究输入",
    items: [
      {
        to: "/collect",
        label: "论文收集",
        description: "检索与订阅",
        icon: Search,
        group: "research-input",
      },
      {
        to: "/papers",
        label: "论文库",
        description: "文献资产",
        icon: FileStack,
        group: "research-input",
      },
    ],
  },
  {
    key: "research-analysis",
    title: "研究分析",
    caption: "研究分析",
    items: [
      {
        to: "/graph",
        label: "研究洞察",
        description: "图谱趋势",
        icon: Network,
        group: "research-analysis",
      },
      {
        to: "/wiki",
        label: "专题综述",
        description: "知识沉淀",
        icon: BookCopy,
        group: "research-analysis",
      },
      {
        to: "/writing",
        label: "写作助手",
        description: "论文写作",
        icon: PenTool,
        group: "research-analysis",
      },
      {
        to: "/brief",
        label: "研究日报",
        description: "每日研究摘要",
        icon: Newspaper,
        group: "research-analysis",
      },
    ],
  },
  {
    key: "execution-secondary",
    title: "执行协作",
    caption: "执行协作",
    items: [
      {
        to: "/projects",
        label: "项目工作区",
        description: "项目与流程",
        icon: FolderKanban,
        group: "execution",
      },
      {
        to: "/tasks",
        label: "任务中心",
        description: "后台任务",
        icon: ListTodo,
        group: "execution",
      },
    ],
  },
  {
    key: "system",
    title: "系统",
    caption: "系统",
    items: [
      {
        to: "/settings",
        label: "设置",
        description: "模型与系统",
        icon: Settings,
        group: "system",
      },
    ],
  },
];

function isRouteSelected(pathname: string, to: string): boolean {
  if (to === "/assistant") {
    return pathname.startsWith("/assistant");
  }
  return pathname === to || pathname.startsWith(`${to}/`);
}

export function findShellNavMatch(pathname: string): ShellNavMatch | null {
  for (const section of shellNavSections) {
    for (const item of section.items) {
      if (isRouteSelected(pathname, item.to)) {
        return { section, item };
      }
    }
  }
  return null;
}

export function resolveRouteMeta(pathname: string): RouteMeta {
  if (pathname === "/" || pathname.startsWith("/dashboard")) {
    return {
      eyebrow: "ResearchOS",
      title: "主页",
      subtitle: "在一屏里查看研究输入、执行状态、项目推进和下一步重点。",
    };
  }
  if (pathname.startsWith("/workbench")) {
    return {
      eyebrow: "项目工作区",
      title: "项目工作区",
      subtitle: "围绕具体研究项目组织工作区、上下文和执行流。",
    };
  }
  if (pathname.startsWith("/assistant")) {
    return {
      eyebrow: "研究助手",
      title: "研究助手",
      subtitle: "在同一桌面里持续推进对话、工作区和自动执行任务。",
    };
  }
  if (pathname === "/projects" || pathname.startsWith("/projects/")) {
    return {
      eyebrow: "项目工作区",
      title: "项目工作区",
      subtitle: "把研究目标拆成项目、工作区和可执行阶段，统一管理交付节奏。",
    };
  }
  if (pathname.startsWith("/collect")) {
    return {
      eyebrow: "论文输入",
      title: "论文收集",
      subtitle: "从检索、订阅和外部导入入口持续扩充研究输入源。",
    };
  }
  if (pathname === "/papers") {
    return {
      eyebrow: "文献资产",
      title: "论文库",
      subtitle: "集中管理论文资产、阅读状态和后续分析材料。",
    };
  }
  if (pathname.startsWith("/papers/")) {
    return {
      eyebrow: "论文详情",
      title: "论文详情",
      subtitle: "查看单篇论文的结构化解读、图表材料与深度分析结果。",
    };
  }
  if (pathname.startsWith("/topics")) {
    return {
      eyebrow: "论文收集",
      title: "论文收集",
      subtitle: "统一汇入待研究主题和新增输入材料。",
    };
  }
  if (pathname.startsWith("/graph")) {
    return {
      eyebrow: "研究洞察",
      title: "研究洞察",
      subtitle: "用图谱、时间线和关系视图捕捉主题演进与关键节点。",
    };
  }
  if (pathname.startsWith("/wiki")) {
    return {
      eyebrow: "专题综述",
      title: "专题综述",
      subtitle: "把零散论文结论沉淀成可以复用的研究知识底座。",
    };
  }
  if (pathname.startsWith("/my-day") || pathname.startsWith("/brief")) {
    return {
      eyebrow: "研究日报",
      title: "研究日报",
      subtitle: "把当天研究进展、重点风险和下一步动作压缩成一页稳定可读的日报。",
    };
  }
  if (pathname.startsWith("/writing")) {
    return {
      eyebrow: "写作助手",
      title: "写作助手",
      subtitle: "把洞察、引用和草稿组织成面向论文或报告的成文流程。",
    };
  }
  if (pathname.startsWith("/tasks")) {
    return {
      eyebrow: "任务中心",
      title: "任务中心",
      subtitle: "追踪后台执行、失败重试和研究流水线的整体运行状态。",
    };
  }
  if (pathname.startsWith("/pipelines")) {
    return {
      eyebrow: "任务中心",
      title: "任务中心",
      subtitle: "统一查看研究流水线与后台作业的执行进展。",
    };
  }
  if (pathname.startsWith("/operations")) {
    return {
      eyebrow: "系统配置",
      title: "系统配置",
      subtitle: "调整模型、服务接入和系统运行参数。",
    };
  }
  if (pathname.startsWith("/settings")) {
    return {
      eyebrow: "系统配置",
      title: "系统配置",
      subtitle: "维护桌面端运行环境、模型接入与系统级偏好设置。",
    };
  }
  if (pathname.startsWith("/email-settings")) {
    return {
      eyebrow: "系统配置",
      title: "系统配置",
      subtitle: "管理通知、账号绑定和与研究流程相关的系统配置。",
    };
  }
  const matched = findShellNavMatch(pathname);
  if (matched) {
    return {
      eyebrow: matched.section.title,
      title: matched.item.label,
      subtitle: matched.item.description,
    };
  }
  return {
    eyebrow: "ResearchOS",
    title: "ResearchOS",
    subtitle: "围绕研究输入、分析和执行协作构建的一体化桌面工作区。",
  };
}
