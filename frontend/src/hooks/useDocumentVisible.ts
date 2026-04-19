import { useEffect, useState } from "react";

function readVisibility(): boolean {
  if (typeof document === "undefined") return true;
  return document.visibilityState === "visible";
}

export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState<boolean>(() => readVisibility());

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const update = () => setVisible(readVisibility());
    document.addEventListener("visibilitychange", update);
    window.addEventListener("focus", update);
    window.addEventListener("blur", update);
    return () => {
      document.removeEventListener("visibilitychange", update);
      window.removeEventListener("focus", update);
      window.removeEventListener("blur", update);
    };
  }, []);

  return visible;
}
