# Project Case Study: Study Assistant Agent Workbench

## 1. What We Built

This project is a local AI learning workbench for `PDF/PPTX` course material. The user uploads a document, the system renders pages, extracts text/layout blocks/formulas, optionally augments PPT pages with a VLM, generates translated reading overlays, and lets the user trigger page-level explanations with citations and quality checks.

The important design choice is that the system is not a single “translate this page” prompt. It is a toolized Agent workflow with visible state:

```text
preprocess -> vision -> translation -> planning -> retrieval -> page tutor -> quality gate -> reflection/retry
```

The GUI exposes this workflow through an `Agent Graph / 技术链路` panel, so a reviewer can inspect which nodes ran, what model chain was used, and how the pipeline maps to `Tool-Use`, `RAG`, `ReAct`, `Reflexion`, and a real `LangGraph` page-tutor subgraph.

## 2. Why This Exists

The target problem is not generic translation. It is the concrete pain of reading technical English lecture decks:

- PPT/PDF pages mix titles, charts, captions, formulas, and screenshots.
- A page often depends on the previous/next page.
- Plain OCR misses chart meaning and embedded visual hints.
- A learning assistant should explain with evidence and quality status, not just produce fluent text.
- Users need to know what the system did, what model was used, and whether the result is reliable.

So the product goal became: make the document readable first, then make deeper explanation available on demand, with inspectable evidence and fallback behavior.

## 3. System Design

### Tool Layer

The preprocessing layer converts documents into model-usable artifacts:

- `PPTX -> PDF` conversion through LibreOffice
- page image rendering through PyMuPDF
- text and `layout_blocks` extraction
- OCR fallback for low-text pages
- formula detection and repair
- optional VLM page reading for PPT pages
- SQLite persistence for pages, blocks, runs, prompts, and explanations

This is the `Tool-Use` part of the system: each step has a bounded input/output and can be inspected or replaced.

### Model Routing Layer

The verified demo uses a hybrid route:

```text
DeepSeek deepseek-chat
  -> main reasoning, translation, planning, page explanation, structured output

OpenAI gpt-5.4-mini
  -> vision-only augmentation for PPT/image-heavy pages
```

This division is deliberate. DeepSeek handles long text and structured reasoning. OpenAI mini is used only when the system needs visual understanding of charts, screenshots, arrows, or embedded slide text.

### Agent Layer

The Agent layer separates responsibilities:

- `Agent T`: generates translated reading overlays from layout blocks.
- `Agent A`: builds document-level memory, keywords, glossary, learning arc, and groups.
- `Agent B`: summarizes groups and provides chapter-level context.
- `Agent C`: explains one page using current page evidence, document memory, group context, and local RAG pages.
- `Quality Gate`: scores explanation quality and records whether citation repair/rewrite/fallback was used.

The page explanation follows a ReAct-style pattern: it uses tool outputs as evidence, then produces a structured teaching response. The page tutor, quality gate, reflection retry, fallback reasoner, and final trace writer are orchestrated by a real LangGraph `StateGraph`; the outer preprocessing, VLM reading, translation, planning, and RAG steps remain ordinary Python tool nodes.

### GUI Layer

The frontend is part of the product, not a thin demo shell. It includes:

- original / translated / bilingual reader modes
- document history and task queue
- page-level explanation and regeneration
- per-page chat history
- settings panels for model/prompt configuration
- `Agent Graph / 技术链路` with node status, model chain, and framework mapping

The GUI is important because it turns hidden Agent behavior into something visible and reviewable.

## 4. Verified Demo

We created and processed a short 3-page PPTX deck about gradient descent and learning rate.

Verified result on page 2:

- `23` `openai_vision` layout blocks were added from VLM page reading.
- DeepSeek generated the page explanation.
- Quality gate passed with score `92.97`.
- `scopePages=[1, 2, 3]`, with no nonexistent page references.
- The UI displayed this model chain:

```text
deepseek:deepseek-chat:agent_c -> flash-citation-repair -> openai:gpt-5.4-mini:vision
```

The visible framework mapping includes:

- `LangGraph`: Agent C uses `StateGraph` for page tutor -> quality gate -> reflection retry/fallback -> finalize state transitions
- `ReAct`: Agent C grounds output in page/tool evidence
- `Reflexion`: quality feedback, citation repair, rewrite/fallback metadata
- `RAG`: local context and scope pages
- `Tool-Use`: conversion, rendering, VLM, OCR, translation, evaluator nodes

## 5. What Was Fixed During Implementation

Several details were corrected to make the project defensible:

- The OpenAI model list was checked before naming the VLM model. The accessible mini model was `gpt-5.4-mini`, not `gpt-5.5-mini`.
- DeepSeek was verified through a real `deepseek-chat` JSON call before using it in the local server.
- PPT visual reading was limited to `source_type == "pptx"` to avoid surprising external VLM calls for ordinary PDFs.
- Agent run metadata was improved so page runs show both the main reasoning model and the vision augmentation path.
- `scopePages` was corrected to report only actual evidence pages, avoiding references to nonexistent pages.
- Agent C quality/retry control was moved from a sequential function body into a real LangGraph `StateGraph`, with `agentFramework=LangGraph` and `agentGraphTrace` written into each explanation payload.
- Test fake API keys were rewritten as obvious unit-test placeholders for public GitHub safety.
- Generated files such as `.env`, databases, runtime logs, `output/`, and `egg-info` are excluded from public source control.

## 6. Validation

The project was verified with:

```text
backend pytest -q
frontend npm run build
browser UI check on local app
```

The browser check confirmed that page 2 displays:

- translated page content
- VLM-derived chart notes
- AI explanation
- `Agent Graph / 技术链路`
- `scopePages=[1, 2, 3]`
- model chain containing DeepSeek Agent C and OpenAI vision

## 7. Current Boundaries

Implemented:

- Local MVP with working backend and GUI.
- Real hybrid `LLM/VLM` routing was tested locally.
- The Agent Graph maps the implementation to common Agent framework concepts.
- Agent C uses the actual LangGraph package for quality/retry orchestration.
- Quality gate and citation repair are implemented.

Limitations:

- It is not a production SaaS.
- LangGraph is currently scoped to Agent C page tutoring, quality routing, reflection retry, fallback, and trace metadata. The outer document pipeline still uses ordinary Python functions for preprocessing, VLM reading, translation, planning, and RAG.
- It is not a full benchmark suite; the current evaluation is a lightweight internal quality gate plus a verified demo run.
- It does not include robotics control, hardware integration, or autonomous actuation.

## 8. Engineering Notes

The core engineering point is that document understanding is decomposed into typed tools, shared state, evidence channels, model routing, quality gates, and retry paths. This keeps each step inspectable and replaceable:

- VLM reading can be swapped without changing the page tutor.
- Agent C can be tested independently of document preprocessing.
- Quality feedback is explicit state, not hidden prompt text.
- The GUI displays model chain, scope pages, framework mapping, and quality status.

## 9. Next Steps

The current implementation is intentionally scoped. Useful next improvements would be:

- Add a small benchmark set for page explanation quality and citation alignment.
- Persist LangGraph node-level timing and error metadata for run comparison.
- Add a visual diff view for original page, translated overlay, and VLM layout blocks.
- Expand the LangGraph orchestration from Agent C to the document-level planner once the page-level graph is stable.
