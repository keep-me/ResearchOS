import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import Markdown from "@/components/Markdown";
import { canSignApiAssetUrl, resolveSignedApiAssetUrl } from "@/services/http";

describe("security-sensitive frontend helpers", () => {
  it("renders KaTeX without trusted javascript links", async () => {
    let capturedTrust: unknown = null;
    Object.defineProperty(window, "katex", {
      configurable: true,
      value: {
        renderToString: (_formula: string, options: { trust?: boolean }) => {
          capturedTrust = options.trust;
          return '<span><a href="javascript:alert(1)">x</a></span>';
        },
      },
    });

    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<Markdown>{"$\\href{javascript:alert(1)}{x}$"}</Markdown>);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(capturedTrust).toBe(false);
    expect(container.innerHTML).not.toContain("javascript:");

    root.unmount();
    delete (window as Window & { katex?: unknown }).katex;
  });

  it("uses short-lived path tokens instead of the session bearer in asset URLs", async () => {
    sessionStorage.setItem("auth_token", "long-session-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ access_token: "short-path-token", expires_in: 90 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const url = await resolveSignedApiAssetUrl("/papers/p1/pdf");

    expect(url).toContain("token=short-path-token");
    expect(url).not.toContain("long-session-token");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/auth/path-token"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ path: "/papers/p1/pdf" }),
      }),
    );

    fetchMock.mockRestore();
  });

  it("signs absolute same-api asset URLs", async () => {
    sessionStorage.setItem("auth_token", "long-session-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ access_token: "absolute-path-token", expires_in: 90 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    expect(canSignApiAssetUrl("http://localhost:8000/papers/p2/pdf")).toBe(true);

    const url = await resolveSignedApiAssetUrl("http://localhost:8000/papers/p2/pdf");

    expect(url).toContain("/papers/p2/pdf");
    expect(url).toContain("token=absolute-path-token");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/auth/path-token"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ path: "/papers/p2/pdf" }),
      }),
    );

    fetchMock.mockRestore();
  });
});
