import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import pdfjsModuleUrl from "pdfjs-dist/build/pdf.min.mjs?url";
import pdfjsWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

type PdfViewport = {
  width: number;
  height: number;
  rotation: number;
  scale: number;
  clone?: (options: { dontFlip?: boolean }) => PdfViewport;
};

type PdfRenderTask = {
  promise: Promise<void>;
  cancel?: () => void;
};

type PdfPageProxy = {
  getViewport: (options: { scale: number; rotation?: number }) => PdfViewport;
  render: (options: {
    canvasContext: CanvasRenderingContext2D;
    viewport: PdfViewport;
    transform?: number[];
  }) => PdfRenderTask;
  streamTextContent?: (options?: { includeMarkedContent?: boolean }) => ReadableStream;
  getTextContent?: (options?: { includeMarkedContent?: boolean }) => Promise<unknown>;
  getAnnotations?: (options?: { intent?: string }) => Promise<unknown[]>;
  cleanup?: () => void;
};

type PdfDocumentProxy = {
  numPages: number;
  annotationStorage?: unknown;
  getPage: (pageNumber: number) => Promise<PdfPageProxy>;
  destroy?: () => Promise<void>;
};

type PdfLoadingTask = {
  promise: Promise<PdfDocumentProxy>;
  destroy?: () => Promise<void>;
};

type TextLayerTask = {
  render: () => Promise<void>;
  cancel?: () => void;
};

type AnnotationLayerTask = {
  render: (params: Record<string, unknown>) => Promise<void> | void;
};

type PdfJsModule = {
  getDocument: (source: { url: string }) => PdfLoadingTask;
  GlobalWorkerOptions: { workerSrc: string };
  TextLayer: new (params: {
    container: HTMLDivElement;
    textContentSource: ReadableStream | unknown;
    viewport: PdfViewport;
  }) => TextLayerTask;
  AnnotationLayer?: new (params: Record<string, unknown>) => AnnotationLayerTask;
};

type PdfContextValue = {
  pdf: PdfDocumentProxy;
  pdfjs: PdfJsModule;
};

interface DocumentProps {
  file: string;
  onLoadSuccess?: (payload: { numPages: number }) => void;
  onLoadError?: (error: Error) => void;
  loading?: ReactNode;
  children: ReactNode;
}

interface PageProps {
  pageNumber: number;
  scale?: number;
  className?: string;
  renderTextLayer?: boolean;
  renderAnnotationLayer?: boolean;
}

const PdfContext = createContext<PdfContextValue | null>(null);

let pdfjsPromise: Promise<PdfJsModule> | null = null;

function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function toError(error: unknown) {
  return error instanceof Error ? error : new Error(String(error));
}

async function loadPdfJs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import(/* @vite-ignore */ pdfjsModuleUrl)
      .then((module) => {
        const pdfjs = module as PdfJsModule;
        pdfjs.GlobalWorkerOptions.workerSrc = pdfjsWorkerUrl;
        return pdfjs;
      })
      .catch((error) => {
        pdfjsPromise = null;
        throw error;
      });
  }
  return pdfjsPromise;
}

function createLinkService() {
  const scrollToPage = (pageNumber: number) => {
    if (!Number.isFinite(pageNumber) || pageNumber < 1) return;
    document
      .querySelector(`[data-pdf-page-number="${Math.floor(pageNumber)}"]`)
      ?.scrollIntoView({ block: "start" });
  };
  return {
    externalLinkTarget: 2,
    externalLinkRel: "noopener noreferrer nofollow",
    eventBus: {
      dispatch() {
        return undefined;
      },
    },
    addLinkAttributes(link: HTMLAnchorElement, url: string, newWindow = false) {
      link.href = url;
      link.rel = "noopener noreferrer nofollow";
      link.target = newWindow ? "_blank" : "_self";
    },
    getDestinationHash() {
      return "";
    },
    getAnchorUrl(hash: string) {
      return hash;
    },
    goToDestination(destination: unknown) {
      if (Array.isArray(destination) && typeof destination[0] === "object") {
        return undefined;
      }
      return undefined;
    },
    setHash(hash: string) {
      const match = String(hash || "").match(/page=(\d+)/i);
      if (match) scrollToPage(Number(match[1]));
    },
    navigateTo() {
      return undefined;
    },
    executeNamedAction(action: string) {
      if (String(action || "").toLowerCase() === "nextpage") {
        const current = document.querySelector("[data-pdf-page-number]");
        const page = Number(current?.getAttribute("data-pdf-page-number") || "0");
        scrollToPage(page + 1);
      }
    },
    executeSetOCGState() {
      return undefined;
    },
  };
}

export function Document({
  file,
  onLoadSuccess,
  onLoadError,
  loading,
  children,
}: DocumentProps) {
  const [contextValue, setContextValue] = useState<PdfContextValue | null>(null);
  const successRef = useRef(onLoadSuccess);
  const errorRef = useRef(onLoadError);

  useEffect(() => {
    successRef.current = onLoadSuccess;
    errorRef.current = onLoadError;
  }, [onLoadError, onLoadSuccess]);

  useEffect(() => {
    let cancelled = false;
    let loadingTask: PdfLoadingTask | null = null;
    let loadedPdf: PdfDocumentProxy | null = null;

    setContextValue(null);

    loadPdfJs()
      .then((pdfjs) => {
        if (cancelled) return null;
        loadingTask = pdfjs.getDocument({ url: file });
        return loadingTask.promise.then((pdf) => ({ pdfjs, pdf }));
      })
      .then((loaded) => {
        if (!loaded) return;
        if (cancelled) {
          void loaded.pdf.destroy?.();
          return;
        }
        loadedPdf = loaded.pdf;
        setContextValue(loaded);
        successRef.current?.({ numPages: loaded.pdf.numPages });
      })
      .catch((error) => {
        if (!cancelled) {
          errorRef.current?.(toError(error));
        }
      });

    return () => {
      cancelled = true;
      setContextValue(null);
      const destroyTarget = loadedPdf ? loadedPdf.destroy?.() : loadingTask?.destroy?.();
      void destroyTarget;
    };
  }, [file]);

  if (!contextValue) {
    return <>{loading ?? null}</>;
  }

  return <PdfContext.Provider value={contextValue}>{children}</PdfContext.Provider>;
}

export function Page({
  pageNumber,
  scale = 1,
  className,
  renderTextLayer = true,
  renderAnnotationLayer = true,
}: PageProps) {
  const context = useContext(PdfContext);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const annotationLayerRef = useRef<HTMLDivElement | null>(null);
  const [dimensions, setDimensions] = useState({ width: 612 * scale, height: 792 * scale });
  const [pageError, setPageError] = useState<string | null>(null);
  const linkService = useMemo(() => createLinkService(), []);

  useEffect(() => {
    if (!context) return;
    const pdfContext: PdfContextValue = context;

    let cancelled = false;
    let renderTask: PdfRenderTask | null = null;
    let textLayerTask: TextLayerTask | null = null;
    let pageProxy: PdfPageProxy | null = null;

    const clearLayer = (element: HTMLElement | null) => {
      if (element) element.innerHTML = "";
    };

    async function renderPage() {
      try {
        setPageError(null);
        const page = await pdfContext.pdf.getPage(pageNumber);
        if (cancelled) return;
        pageProxy = page;

        const viewport = page.getViewport({ scale });
        setDimensions({ width: viewport.width, height: viewport.height });

        const canvas = canvasRef.current;
        const canvasContext = canvas?.getContext("2d");
        if (!canvas || !canvasContext) return;

        const outputScale = Math.max(window.devicePixelRatio || 1, 1);
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;

        renderTask = page.render({
          canvasContext,
          viewport,
          transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
        });
        await renderTask.promise;

        if (cancelled) return;

        if (renderTextLayer) {
          const layer = textLayerRef.current;
          if (layer) {
            clearLayer(layer);
            const textContentSource = page.streamTextContent
              ? page.streamTextContent({ includeMarkedContent: true })
              : await page.getTextContent?.({ includeMarkedContent: true });
            if (textContentSource && !cancelled) {
              textLayerTask = new pdfContext.pdfjs.TextLayer({
                container: layer,
                textContentSource,
                viewport,
              });
              await textLayerTask.render();
              if (!cancelled) {
                const end = document.createElement("div");
                end.className = "endOfContent";
                layer.append(end);
              }
            }
          }
        } else {
          clearLayer(textLayerRef.current);
        }

        if (renderAnnotationLayer && pdfContext.pdfjs.AnnotationLayer && page.getAnnotations) {
          const layer = annotationLayerRef.current;
          if (layer) {
            clearLayer(layer);
            const annotations = await page.getAnnotations({ intent: "display" });
            if (!cancelled) {
              const annotationViewport = viewport.clone?.({ dontFlip: true }) ?? viewport;
              const annotationLayer = new pdfContext.pdfjs.AnnotationLayer({
                accessibilityManager: null,
                annotationCanvasMap: null,
                annotationEditorUIManager: null,
                annotationStorage: pdfContext.pdf.annotationStorage,
                commentManager: null,
                div: layer,
                linkService,
                page,
                structTreeLayer: null,
                viewport: annotationViewport,
              });
              await annotationLayer.render({
                annotations,
                annotationStorage: pdfContext.pdf.annotationStorage,
                div: layer,
                linkService,
                page,
                renderForms: true,
                viewport: annotationViewport,
              });
            }
          }
        } else {
          clearLayer(annotationLayerRef.current);
        }
      } catch (error) {
        if (!cancelled) {
          setPageError(toError(error).message);
        }
      }
    }

    void renderPage();

    return () => {
      cancelled = true;
      renderTask?.cancel?.();
      textLayerTask?.cancel?.();
      clearLayer(textLayerRef.current);
      clearLayer(annotationLayerRef.current);
      pageProxy?.cleanup?.();
    };
  }, [context, linkService, pageNumber, renderAnnotationLayer, renderTextLayer, scale]);

  const handleTextLayerMouseDown = () => {
    textLayerRef.current?.classList.add("selecting");
  };

  const handleTextLayerMouseUp = () => {
    textLayerRef.current?.classList.remove("selecting");
  };

  return (
    <div
      className={classNames("react-pdf__Page", className)}
      data-pdf-page-number={pageNumber}
      style={{ position: "relative", width: dimensions.width, height: dimensions.height }}
    >
      <canvas ref={canvasRef} className="react-pdf__Page__canvas block" />
      {renderTextLayer ? (
        <div
          ref={textLayerRef}
          className="react-pdf__Page__textContent textLayer"
          onMouseDown={handleTextLayerMouseDown}
          onMouseUp={handleTextLayerMouseUp}
        />
      ) : null}
      {renderAnnotationLayer ? (
        <div ref={annotationLayerRef} className="react-pdf__Page__annotations annotationLayer" />
      ) : null}
      {pageError ? (
        <div className="absolute inset-0 flex items-center justify-center bg-surface text-xs text-red-300">
          {pageError}
        </div>
      ) : null}
    </div>
  );
}
