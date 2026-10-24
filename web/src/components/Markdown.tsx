import { useMemo } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";

/** Markdown to sanitized HTML. Raw HTML in the input is removed by DOMPurify; links open in a new tab. */
export function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false, gfm: true }) as string;
  const clean = DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "style", "form", "input", "button", "iframe"],
    FORBID_ATTR: ["style"],
    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|#|\/(?!\/)|[a-z0-9_.-]+(?:\/|$))/i,
  });
  const doc = new DOMParser().parseFromString(clean, "text/html");
  doc.querySelectorAll("a[href]").forEach((a) => {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  });
  return doc.body.innerHTML;
}

export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}
