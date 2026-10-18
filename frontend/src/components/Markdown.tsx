import { useMemo, useEffect, useRef } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

marked.setOptions({ breaks: true, gfm: true });

/** Render sanitized markdown; add a Copy button to every code block. */
export function Markdown({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);

  const html = useMemo(() => {
    const raw = marked.parse(text ?? "", { async: false }) as string;
    return DOMPurify.sanitize(raw, { ADD_ATTR: ["target"] });
  }, [text]);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    root.querySelectorAll("pre").forEach((pre) => {
      if (pre.querySelector(".copy-code")) return;
      const btn = document.createElement("button");
      btn.className = "copy-code";
      btn.textContent = "Copy";
      btn.addEventListener("click", () => {
        const code = pre.querySelector("code")?.textContent ?? "";
        navigator.clipboard.writeText(code);
        btn.textContent = "Copied ✓";
        setTimeout(() => (btn.textContent = "Copy"), 1200);
      });
      pre.appendChild(btn);
    });
  }, [html]);

  return <div ref={ref} className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}
