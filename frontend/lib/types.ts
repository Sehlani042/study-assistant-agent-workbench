export type DocumentStatus = {
  document_id: string;
  status: string;
  stage: string;
  stage_label: string;
  error: string | null;
  progress: {
    total_pages: number;
    processed_pages: number;
    percent: number;
  };
  pipeline_detail: {
    stage_code: string;
    running_agent: string;
    active_workers: number;
    queued_pages: number;
    done_pages: number;
    failed_pages: number;
    retry_pages: number;
    current_pages: number[];
    page_status_counts: Record<string, number>;
    current_page_details: {
      page_no: number;
      status: string;
      reason: string;
      repairable: boolean;
      model_used: string;
    }[];
    failed_page_details: {
      page_no: number;
      status: string;
      reason: string;
      repairable: boolean;
      model_used: string;
    }[];
    repairable_pages: number[];
    stage_started_at: string;
    total_started_at: string;
    updated_at: string;
    stage_elapsed_seconds: number;
    total_elapsed_seconds: number;
    c1_timeout_pages: number;
    avg_c1_latency_ms: number;
    p95_c1_latency_ms: number;
    translation_pending: number;
    translation_done: number;
    translation_failed: number;
    pro_escalation_pages: number;
    last_resort_pages: number;
    llm_error_counts: Record<string, number>;
    model_path_counts: Record<string, number>;
    quality_fail_streak: number;
    adaptive_worker_reason: string;
    coverage_lang_mode: string;
    last_error: string;
  };
};

export type DocumentHistoryItem = {
  document_id: string;
  original_filename: string;
  source_type: string;
  status: string;
  stage: string;
  stage_label: string;
  error: string | null;
  progress: {
    total_pages: number;
    processed_pages: number;
    percent: number;
  };
  last_page_no: number;
  latest_run_id?: string | null;
  has_active_job: boolean;
  translation_ready_pages: number;
  translation_total_pages: number;
  explained_pages: number;
  job_type: string;
  created_at: string;
  updated_at: string;
};

export type DocumentHistoryResponse = {
  items: DocumentHistoryItem[];
};

export type OutlineGroup = {
  id: string;
  title: string;
  page_start: number;
  page_end: number;
  summary?: string;
  key_concepts?: string[];
  prerequisites?: string[];
  misconceptions?: string[];
};

export type Outline = {
  document_id: string;
  global_summary: string;
  keywords: string[];
  groups: OutlineGroup[];
  learning_arc: { from_group: string; to_group: string; why: string }[];
};

export type PageExplanation = {
  overview: string;
  keyPoints: string[];
  conceptLinks: string[];
  formulaBlocks: { latex: string; meaning: string; sourceSpan: string }[];
  citations: { pageNo: number; span: string; quote: string }[];
  confidence: number;
  quality: {
    score: number;
    coverage: number;
    citationScore: number;
    formulaRenderRate: number;
    terminologyConsistency: number;
    continuityScore?: number;
    specificityScore?: number;
    actionabilityScore?: number;
    languageScore?: number;
    boilerplateHits?: number;
    hardFailed?: boolean;
    pass: boolean;
    feedback: string[];
  };
  literalTranslation?: string;
  translationStatus?: "pending" | "ready" | "failed" | string;
  translationUpdatedAt?: string;
  translationError?: string;
  isDraft?: boolean;
  processingStage?: string;
  teaching?: {
    definition: string;
    intuition: string;
    example: string;
    focus: string;
    pitfall: string;
  };
  memoryUsed: {
    globalVersion: string;
    groupId: string;
    localPages: number[];
  };
  scaffold?: {
    quick30: string[];
    understand2m: string[];
    master5m: string[];
  };
  continuity?: {
    prevBridge: string;
    thisPageNew: string;
    nextPreview: string;
  };
  microTask?: {
    doNow: string;
    checkQuestion: string;
    answerHint: string;
  };
  clarity?: {
    conclusion: string;
    steps: string[];
    example: string;
  };
  evidenceBlocks?: {
    kind: "conclusion" | "step" | "example" | string;
    claim: string;
    citations: { pageNo: number; span: string; quote: string }[];
  }[];
  scopePages?: number[];
  statusHint?: string;
  version: number;
};

export type PagePayload = {
  document_id: string;
  page_no: number;
  group_id: string | null;
  image_url: string;
  text_preview: string;
  formulas: { latex: string; sourceSpan: string; valid: boolean }[];
  explanation: PageExplanation | null;
  reader_mode_default?: "original" | "translated" | "bilingual" | string;
  layout_blocks?: {
    id: string;
    text: string;
    bbox: { x: number; y: number; width: number; height: number };
    kind: string;
    source: string;
    confidence?: number;
    font_size?: number;
    reading_order?: number;
  }[];
  translation_blocks?: {
    id: string;
    block_id: string;
    text: string;
    bbox: { x: number; y: number; width: number; height: number };
    kind: string;
    source: string;
    confidence?: number;
    line_count?: number;
    fitted_font_size?: number;
    reading_order?: number;
    status?: string;
  }[];
  untranslated_blocks?: {
    block_id: string;
    text: string;
    kind: string;
    source: string;
    reason: string;
    confidence?: number;
  }[];
  translation_overlay_status?: "pending" | "ready" | "partial" | "unavailable" | string;
  literal_translation?: string;
  translation_updated_at?: string | null;
  statusHint?: string;
  view_model?: { mode: string; collapsedSections: string[] };
  quality_hint?: string;
  content_density?: { visible_blocks: number; visible_tokens: number };
  reading_tabs?: string[];
  default_tab?: string;
  run_id?: string | null;
  latest_run_status?: string;
  chapter_nav?: {
    groups: { id: string; title: string; page_start: number; page_end: number; explained_pages: number }[];
    current_group_id: string;
  };
  evidence_drawer?: {
    scope_pages: number[];
    citations: { pageNo: number; span: string; quote: string }[];
    evidence_blocks: {
      kind: string;
      claim: string;
      citations: { pageNo: number; span: string; quote: string }[];
    }[];
    memory_used: Record<string, unknown>;
    run?: {
      run_id: string;
      status: string;
      trigger_type: string;
      scope_type: string;
      target_page_no?: number | null;
    } | null;
  };
  agent_graph?: {
    nodes: {
      id: string;
      label: string;
      status: string;
      requirement: string;
      evidence: string;
    }[];
    edges: { from: string; to: string }[];
    run?: {
      status: string;
      model_chain: string[];
    };
    framework_mapping: Record<string, string>;
  };
};

export type LearningPreferences = {
  learner_level: "beginner" | "intermediate" | "advanced";
  learning_goal: "understand" | "learn_and_apply" | "exam";
  depth_mode: "quick" | "standard" | "deep";
  attention_support: "standard" | "adhd_friendly";
  source?: "default" | "personal" | string;
};

export type DocumentRunSummary = {
  run_id: string;
  document_id: string;
  trigger_type: string;
  scope_type: string;
  target_page_no?: number | null;
  status: string;
  error?: string | null;
  learning_profile: LearningPreferences;
  created_at: string;
  started_at: string;
  finished_at?: string | null;
  updated_at: string;
};

export type DocumentRunDetail = DocumentRunSummary & {
  job_id?: string | null;
  prompt_snapshot: Record<string, string>;
  model_chain: string[];
  quality_stats: Record<string, unknown>;
};

export type DocumentRunListResponse = {
  items: DocumentRunSummary[];
};

export type ExplanationPreviewResponse = {
  explanation_preview: PageExplanation;
  translation_preview: string;
  quality_preview: Record<string, unknown>;
  model_meta: Record<string, unknown>;
};

export type ChatAnswer = {
  answer: string;
  citations: { pageNo: number; span: string; quote: string }[];
  relatedContext: string[];
  scopePages?: number[];
};

export type ChatHistoryItem = {
  id: string;
  page_no: number;
  question: string;
  answer: ChatAnswer;
  created_at: string;
};

export type ChatHistoryResponse = {
  document_id: string;
  page_no: number;
  items: ChatHistoryItem[];
};

export type PromptConfig = {
  agent_a_instruction: string;
  agent_b_instruction: string;
  agent_c_instruction: string;
  chat_instruction: string;
  formula_instruction: string;
  source?: "default" | "personal";
  has_custom?: boolean;
};

export type PromptFields = {
  agent_a_instruction: string;
  agent_b_instruction: string;
  agent_c_instruction: string;
  chat_instruction: string;
  formula_instruction: string;
};

export type DocumentPromptSnapshot = {
  document_id: string;
  prompt_profile: "default" | "personal";
  task_prompt: string | null;
  prompt_config: PromptFields;
};

export type UserPayload = {
  id: string;
  username: string;
  email?: string | null;
  email_verified?: boolean;
  role: string;
  is_active: boolean;
  can_use_shared_key?: boolean;
  permissions?: {
    can_manage_accounts: boolean;
    can_manage_prompts: boolean;
    can_manage_shared_keys: boolean;
  };
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  user: UserPayload;
};

export type AuthPolicy = {
  username: {
    pattern: string;
    min_length: number;
    max_length: number;
    normalization: string;
    description: string;
  };
  password: {
    min_length: number;
    max_length: number;
    require_letters: boolean;
    require_numbers: boolean;
    forbid_whitespace: boolean;
    description: string;
  };
  registration?: {
    mode: "open" | "invite" | "closed";
    invite_required: boolean;
    enabled: boolean;
    email_verification_required?: boolean;
    email_code_resend_seconds?: number;
  };
};

export type LLMAccess = {
  provider: string;
  model: string;
  display_label?: string;
  resolved_model?: string;
  resolution_source?: string;
  can_use_shared_key: boolean;
  requires_personal_key: boolean;
  has_personal_key: boolean;
  providers: Record<
    string,
    {
      provider: string;
      model: string;
      has_shared_key_configured: boolean;
      can_use_shared_key: boolean;
      requires_personal_key: boolean;
      has_personal_key: boolean;
      display_label?: string;
      resolved_model?: string;
      resolution_source?: string;
      recommended_models?: {
        id: string;
        display_label: string;
        resolved_model: string;
        resolution_source: string;
      }[];
    }
  >;
};

export type LLMRecommendedModel = {
  id: string;
  display_label: string;
  resolved_model: string;
  resolution_source: string;
};

export type LLMProviderOption = {
  id: string;
  label: string;
  recommended_models: LLMRecommendedModel[];
};

export type LLMSettings = {
  global_default: {
    default_provider: string;
    default_models: Record<string, string>;
  };
  user_default: {
    provider: string;
    model: string;
    display_label?: string;
    resolved_model?: string;
    resolution_source?: string;
  };
  effective: {
    provider: string;
    model: string;
    display_label?: string;
    resolved_model?: string;
    resolution_source?: string;
  };
  providers: LLMProviderOption[];
};

export type LLMFallbackChainSettings = {
  global_default: string[];
  user_default: string[];
  effective: string[];
  source: "global" | "user" | string;
};

export type SharedKeyInvite = {
  token: string;
  invite_url: string;
  expires_at: string;
  max_uses: number;
  note: string | null;
};

export type RegistrationInvite = {
  code: string;
  invite_url: string;
  expires_at: string;
  max_uses: number;
  used_count: number;
  remaining_uses: number;
  revoked: boolean;
  note: string | null;
  created_at: string;
};

export type RegistrationInviteListResponse = {
  items: RegistrationInvite[];
};

export type Language = "zh" | "en";

export type ChatTurn = {
  role: "ask" | "answer";
  text: string;
  citations?: ChatAnswer["citations"];
  scopePages?: number[];
};

export type DashboardPage = "study" | "queue" | "history" | "settings" | "prompts" | "accounts" | "lab";

export type PermissionDraft = {
  can_manage_accounts: boolean;
  can_manage_prompts: boolean;
  can_manage_shared_keys: boolean;
};

export type UserEditDraft = {
  role: "admin" | "user";
  is_active: boolean;
  can_use_shared_key: boolean;
  permissions: PermissionDraft;
};

export type PendingRun =
  | { kind: "upload"; file: File; learningProfile: LearningPreferences }
  | { kind: "regenerate"; documentId: string; pageNo: number; learningProfile: LearningPreferences }
  | { kind: "explain"; documentId: string; pageNo: number; learningProfile: LearningPreferences };

export type PromptOverridePayload = Partial<
  Pick<
    PromptConfig,
    "agent_a_instruction" | "agent_b_instruction" | "agent_c_instruction" | "chat_instruction" | "formula_instruction"
  >
>;

export type PromptInstructionKey =
  | "agent_a_instruction"
  | "agent_b_instruction"
  | "agent_c_instruction"
  | "chat_instruction"
  | "formula_instruction";
