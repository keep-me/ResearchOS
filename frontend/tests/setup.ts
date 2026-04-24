import { afterEach } from "vitest";

afterEach(() => {
  document.body.innerHTML = "";
  sessionStorage.clear();
  localStorage.clear();
});
