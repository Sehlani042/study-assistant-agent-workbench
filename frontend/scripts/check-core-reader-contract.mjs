import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

const files = {
  page: readFileSync(join(root, "app/page.tsx"), "utf8"),
  studyPanel: readFileSync(join(root, "components/StudyPanel.tsx"), "utf8"),
  css: readFileSync(join(root, "app/globals.css"), "utf8"),
};

const checks = [
  {
    name: "StudyPanel renders the reader-first shell",
    pass: files.studyPanel.includes("core-reader-shell"),
  },
  {
    name: "StudyPanel has a dedicated document reader pane",
    pass: files.studyPanel.includes("core-reader-document-pane"),
  },
  {
    name: "StudyPanel has a dedicated AI assistant pane",
    pass: files.studyPanel.includes("core-reader-assistant-pane"),
  },
  {
    name: "StudyPanel has a primary explanation panel",
    pass: files.studyPanel.includes("assistant-primary-panel"),
  },
  {
    name: "CSS defines the reader-first shell",
    pass: files.css.includes(".core-reader-shell"),
  },
  {
    name: "CSS defines compact chat behavior",
    pass: files.css.includes(".core-chat-section"),
  },
  {
    name: "Page hides verbose pipeline details from the default status bar",
    pass: !files.page.includes("study.pipelineDetailText && <span"),
  },
  {
    name: "StudyPanel exposes Agent Graph as a collapsed audit trail",
    pass: files.studyPanel.includes("Agent Graph / 技术链路"),
  },
  {
    name: "StudyPanel keeps framework mapping out of the primary reading flow",
    pass: files.studyPanel.includes("agent-framework-map"),
  },
];

const failed = checks.filter((check) => !check.pass);

for (const check of checks) {
  console.log(`${check.pass ? "PASS" : "FAIL"} ${check.name}`);
}

if (failed.length > 0) {
  console.error(`\n${failed.length} reader-first UI contract check(s) failed.`);
  process.exit(1);
}
