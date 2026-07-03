"use client";

import katex from "katex";

type LatexBlockProps = {
  latex: string;
};

export function LatexBlock({ latex }: LatexBlockProps) {
  let html = "";
  try {
    html = katex.renderToString(latex || "", {
      displayMode: true,
      throwOnError: false,
      strict: "ignore",
    });
  } catch {
    html = katex.renderToString("\\text{LaTeX render error}", {
      displayMode: true,
      throwOnError: false,
      strict: "ignore",
    });
  }

  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}
