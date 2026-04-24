import { cn } from "@/lib/utils";
import { assistantWorkspaceApi } from "@/services/api";
import type { AssistantWorkspaceTerminalSessionInfo } from "@/types";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef } from "react";

export type WorkspaceTerminalState = "connecting" | "ready" | "closed" | "error";

interface WorkspaceTerminalProps {
  sessionId: string;
  className?: string;
  onStateChange?: (state: WorkspaceTerminalState) => void;
  onSessionInfo?: (info: AssistantWorkspaceTerminalSessionInfo) => void;
  onError?: (message: string) => void;
  onExit?: (exitCode: number | null) => void;
}

export function WorkspaceTerminal({
  sessionId,
  className,
  onStateChange,
  onSessionInfo,
  onError,
  onExit,
}: WorkspaceTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const stateRef = useRef<WorkspaceTerminalState>("connecting");
  const expectedCloseRef = useRef(false);
  const onStateChangeRef = useRef(onStateChange);
  const onSessionInfoRef = useRef(onSessionInfo);
  const onErrorRef = useRef(onError);
  const onExitRef = useRef(onExit);

  useEffect(() => {
    onStateChangeRef.current = onStateChange;
    onSessionInfoRef.current = onSessionInfo;
    onErrorRef.current = onError;
    onExitRef.current = onExit;
  }, [onError, onExit, onSessionInfo, onStateChange]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    const terminal = new Terminal({
      allowTransparency: true,
      convertEol: false,
      cursorBlink: true,
      fontFamily: "'Cascadia Mono', 'JetBrains Mono', 'Fira Code', monospace",
      fontSize: 12,
      lineHeight: 1.18,
      scrollback: 5000,
      theme: {
        background: "#1e1e1e",
        foreground: "#d4d4d4",
        cursor: "#aeafad",
        selectionBackground: "rgba(255, 255, 255, 0.18)",
        black: "#000000",
        red: "#cd3131",
        green: "#0dbc79",
        yellow: "#e5e510",
        blue: "#2472c8",
        magenta: "#bc3fbc",
        cyan: "#11a8cd",
        white: "#e5e5e5",
        brightBlack: "#666666",
        brightRed: "#f14c4c",
        brightGreen: "#23d18b",
        brightYellow: "#f5f543",
        brightBlue: "#3b8eea",
        brightMagenta: "#d670d6",
        brightCyan: "#29b8db",
        brightWhite: "#ffffff",
      },
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(container);
    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;

    const notifyState = (next: WorkspaceTerminalState) => {
      stateRef.current = next;
      onStateChangeRef.current?.(next);
    };

    const sendResize = () => {
      const activeTerminal = terminalRef.current;
      const activeFitAddon = fitAddonRef.current;
      const activeSocket = socketRef.current;
      if (!activeTerminal || !activeFitAddon) return;
      try {
        activeFitAddon.fit();
      } catch {
        return;
      }
      if (activeSocket?.readyState === WebSocket.OPEN) {
        activeSocket.send(JSON.stringify({
          type: "resize",
          cols: activeTerminal.cols,
          rows: activeTerminal.rows,
        }));
      }
    };

    notifyState("connecting");
    const resizeObserver = new ResizeObserver(() => {
      sendResize();
    });
    resizeObserver.observe(container);
    const handleWindowResize = () => {
      sendResize();
    };
    window.addEventListener("resize", handleWindowResize);

    const inputDisposable = terminal.onData((data) => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data }));
      }
    });

    requestAnimationFrame(() => {
      sendResize();
    });

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", handleWindowResize);
      inputDisposable.dispose();
      socketRef.current?.close();
      socketRef.current = null;
      fitAddonRef.current = null;
      terminalRef.current?.dispose();
      terminalRef.current = null;
    };
  }, []);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal || !sessionId) return undefined;

    terminal.clear();
    terminal.reset();
    stateRef.current = "connecting";
    onStateChangeRef.current?.("connecting");
    expectedCloseRef.current = false;

    let disposed = false;
    let socket: WebSocket | null = null;

    const attachSocket = (nextSocket: WebSocket) => {
      socket = nextSocket;
      socketRef.current = nextSocket;

      nextSocket.onopen = () => {
      const fitAddon = fitAddonRef.current;
      if (fitAddon) {
        try {
          fitAddon.fit();
        } catch {
          // ignore initial fit failure before layout settles
        }
      }
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({
          type: "resize",
          cols: terminal.cols,
          rows: terminal.rows,
        }));
      }
      };

      nextSocket.onmessage = (event) => {
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(String(event.data || "{}"));
      } catch {
        return;
      }
      const messageType = String(payload.type || "");
      if (messageType === "ready") {
        terminal.clear();
        terminal.reset();
        const sessionInfo = payload.session as AssistantWorkspaceTerminalSessionInfo | undefined;
        if (sessionInfo) {
          onSessionInfoRef.current?.(sessionInfo);
        }
        stateRef.current = "ready";
        onStateChangeRef.current?.("ready");
        return;
      }
      if (messageType === "output") {
        terminal.write(String(payload.data || ""));
        return;
      }
      if (messageType === "error") {
        const message = String(payload.message || "终端连接异常");
        terminal.writeln(`\r\n[terminal error] ${message}`);
        stateRef.current = "error";
        onStateChangeRef.current?.("error");
        onErrorRef.current?.(message);
        return;
      }
      if (messageType === "exit") {
        const exitCode = typeof payload.exit_code === "number" ? payload.exit_code : null;
        terminal.writeln(`\r\n[terminal exited${exitCode == null ? "" : `: ${exitCode}`}]`);
        stateRef.current = "closed";
        onStateChangeRef.current?.("closed");
        onExitRef.current?.(exitCode);
      }
      };

      nextSocket.onerror = () => {
      if (expectedCloseRef.current || stateRef.current === "closed") return;
      stateRef.current = "error";
      onStateChangeRef.current?.("error");
      onErrorRef.current?.("终端 WebSocket 连接失败");
      };

      nextSocket.onclose = (event) => {
      if (socketRef.current === nextSocket) {
        socketRef.current = null;
      }
      if (expectedCloseRef.current) {
        return;
      }
      if (stateRef.current === "connecting") {
        const message = event.reason || "终端连接在建立前被关闭";
        terminal.writeln(`\r\n[terminal error] ${message}`);
        stateRef.current = "error";
        onStateChangeRef.current?.("error");
        onErrorRef.current?.(message);
        return;
      }
      if (stateRef.current === "ready") {
        stateRef.current = "closed";
        onStateChangeRef.current?.("closed");
      }
      };
    };

    assistantWorkspaceApi.terminalWebSocketUrl(sessionId)
      .then((url) => {
        if (disposed) return;
        attachSocket(new WebSocket(url));
      })
      .catch((error) => {
        if (disposed) return;
        const message = error instanceof Error ? error.message : "终端 WebSocket 连接失败";
        stateRef.current = "error";
        onStateChangeRef.current?.("error");
        onErrorRef.current?.(message);
      });

    return () => {
      disposed = true;
      expectedCloseRef.current = true;
      if (socket && socketRef.current === socket) {
        socketRef.current = null;
      }
      socket?.close();
    };
  }, [sessionId]);

  return <div ref={containerRef} className={cn("h-full w-full overflow-hidden", className)} />;
}
