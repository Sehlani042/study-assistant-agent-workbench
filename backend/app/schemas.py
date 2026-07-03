from __future__ import annotations

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    document_id: str
    job_id: str


class LearningPreferencesPayload(BaseModel):
    learner_level: str = "beginner"
    learning_goal: str = "understand"
    depth_mode: str = "standard"
    attention_support: str = "adhd_friendly"


class ProgressPayload(BaseModel):
    total_pages: int
    processed_pages: int
    percent: float


class PipelineDetailPayload(BaseModel):
    stage_code: str
    running_agent: str
    active_workers: int
    queued_pages: int
    done_pages: int
    failed_pages: int
    retry_pages: int
    current_pages: list[int] = Field(default_factory=list)
    page_status_counts: dict[str, int] = Field(default_factory=dict)
    current_page_details: list[dict] = Field(default_factory=list)
    failed_page_details: list[dict] = Field(default_factory=list)
    repairable_pages: list[int] = Field(default_factory=list)
    stage_started_at: str = ""
    total_started_at: str = ""
    updated_at: str = ""
    stage_elapsed_seconds: float = 0.0
    total_elapsed_seconds: float = 0.0
    c1_timeout_pages: int = 0
    avg_c1_latency_ms: float = 0.0
    p95_c1_latency_ms: float = 0.0
    translation_pending: int = 0
    translation_done: int = 0
    translation_failed: int = 0
    pro_escalation_pages: int = 0
    last_resort_pages: int = 0
    llm_error_counts: dict[str, int] = Field(default_factory=dict)
    model_path_counts: dict[str, int] = Field(default_factory=dict)
    quality_fail_streak: int = 0
    adaptive_worker_reason: str = ""
    coverage_lang_mode: str = ""
    last_error: str = ""


class DocumentStatusResponse(BaseModel):
    document_id: str
    status: str
    stage: str
    stage_label: str
    error: str | None
    progress: ProgressPayload
    pipeline_detail: PipelineDetailPayload


class DocumentListItem(BaseModel):
    document_id: str
    original_filename: str
    source_type: str
    status: str
    stage: str
    stage_label: str
    error: str | None
    progress: ProgressPayload
    last_page_no: int = 1
    latest_run_id: str | None = None
    has_active_job: bool = False
    translation_ready_pages: int = 0
    translation_total_pages: int = 0
    explained_pages: int = 0
    job_type: str = "translate_document"
    created_at: str
    updated_at: str


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem] = Field(default_factory=list)


class GroupPayload(BaseModel):
    id: str
    title: str
    page_start: int
    page_end: int
    summary: str | None = None
    key_concepts: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    misconceptions: list[str] = Field(default_factory=list)


class LearningArcPayload(BaseModel):
    from_group: str
    to_group: str
    why: str


class OutlineResponse(BaseModel):
    document_id: str
    global_summary: str
    keywords: list[str] = Field(default_factory=list)
    groups: list[GroupPayload] = Field(default_factory=list)
    learning_arc: list[LearningArcPayload] = Field(default_factory=list)


class PageResponse(BaseModel):
    document_id: str
    page_no: int
    group_id: str | None
    image_url: str
    text_preview: str
    formulas: list[dict] = Field(default_factory=list)
    explanation: dict | None = None
    reader_mode_default: str = "translated"
    layout_blocks: list[dict] = Field(default_factory=list)
    translation_blocks: list[dict] = Field(default_factory=list)
    untranslated_blocks: list[dict] = Field(default_factory=list)
    translation_overlay_status: str = "pending"
    literal_translation: str = ""
    translation_updated_at: str | None = None
    statusHint: str = ""
    view_model: dict[str, object] = Field(default_factory=dict)
    quality_hint: str = ""
    content_density: dict[str, int] = Field(default_factory=dict)
    reading_tabs: list[str] = Field(default_factory=list)
    default_tab: str = "translate"
    run_id: str | None = None
    latest_run_status: str = ""
    chapter_nav: dict[str, object] = Field(default_factory=dict)
    evidence_drawer: dict[str, object] = Field(default_factory=dict)
    agent_graph: dict[str, object] = Field(default_factory=dict)


class RegenerateResponse(BaseModel):
    document_id: str
    page_no: int
    run_id: str
    explanation: dict


class DocumentRunSummary(BaseModel):
    run_id: str
    document_id: str
    trigger_type: str
    scope_type: str
    target_page_no: int | None = None
    status: str
    error: str | None = None
    learning_profile: LearningPreferencesPayload = Field(default_factory=LearningPreferencesPayload)
    created_at: str
    started_at: str
    finished_at: str | None = None
    updated_at: str


class DocumentRunDetail(DocumentRunSummary):
    job_id: str | None = None
    prompt_snapshot: dict[str, str] = Field(default_factory=dict)
    model_chain: list[str] = Field(default_factory=list)
    quality_stats: dict[str, object] = Field(default_factory=dict)


class DocumentRunListResponse(BaseModel):
    items: list[DocumentRunSummary] = Field(default_factory=list)


class LearningPreferencesResponse(LearningPreferencesPayload):
    source: str = "default"


class LearningPreferencesUpdateRequest(BaseModel):
    learner_level: str | None = None
    learning_goal: str | None = None
    depth_mode: str | None = None
    attention_support: str | None = None


class ExplanationPreviewRequest(LearningPreferencesPayload):
    page_text: str
    formulas: list[dict] = Field(default_factory=list)
    language: str = "zh"
    prompt_profile: str = "personal"
    task_prompt: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    llm_provider: str | None = None
    llm_model: str | None = None


class ExplanationPreviewResponse(BaseModel):
    explanation_preview: dict
    translation_preview: str
    quality_preview: dict
    model_meta: dict[str, object] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    question: str
    language: str = "zh"


class ChatResponse(BaseModel):
    document_id: str
    page_no: int
    answer: dict


class ChatHistoryItem(BaseModel):
    id: str
    page_no: int
    question: str
    answer: dict
    created_at: str


class ChatHistoryResponse(BaseModel):
    document_id: str
    page_no: int
    items: list[ChatHistoryItem] = Field(default_factory=list)


class PromptConfigPayload(BaseModel):
    agent_a_instruction: str
    agent_b_instruction: str
    agent_c_instruction: str
    chat_instruction: str
    formula_instruction: str
    source: str = "default"
    has_custom: bool = False


class PromptConfigUpdateRequest(BaseModel):
    agent_a_instruction: str | None = None
    agent_b_instruction: str | None = None
    agent_c_instruction: str | None = None
    chat_instruction: str | None = None
    formula_instruction: str | None = None


class PromptFields(BaseModel):
    agent_a_instruction: str
    agent_b_instruction: str
    agent_c_instruction: str
    chat_instruction: str
    formula_instruction: str


class DocumentPromptSnapshotResponse(BaseModel):
    document_id: str
    prompt_profile: str
    task_prompt: str | None = None
    prompt_config: PromptFields


class UserPermissionsPayload(BaseModel):
    can_manage_accounts: bool = False
    can_manage_prompts: bool = False
    can_manage_shared_keys: bool = False


class UserPayload(BaseModel):
    id: str
    username: str
    email: str | None = None
    email_verified: bool = False
    role: str
    is_active: bool
    can_use_shared_key: bool = False
    permissions: UserPermissionsPayload = Field(default_factory=UserPermissionsPayload)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPayload


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    is_active: bool = True
    can_use_shared_key: bool = False
    permissions: UserPermissionsPayload = Field(default_factory=UserPermissionsPayload)


class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    can_use_shared_key: bool | None = None
    permissions: UserPermissionsPayload | None = None


class AuthPolicyUsername(BaseModel):
    pattern: str
    min_length: int
    max_length: int
    normalization: str
    description: str


class AuthPolicyPassword(BaseModel):
    min_length: int
    max_length: int
    require_letters: bool
    require_numbers: bool
    forbid_whitespace: bool
    description: str


class AuthPolicyResponse(BaseModel):
    username: AuthPolicyUsername
    password: AuthPolicyPassword
    registration: dict[str, object] = Field(default_factory=dict)


class LLMAccessResponse(BaseModel):
    provider: str
    model: str = ""
    display_label: str = ""
    resolved_model: str = ""
    resolution_source: str = "exact"
    can_use_shared_key: bool
    requires_personal_key: bool
    has_personal_key: bool
    providers: dict[str, dict] = Field(default_factory=dict)


class SetPersonalLLMKeyRequest(BaseModel):
    provider: str | None = None
    api_key: str


class SetPersonalLLMKeyResponse(BaseModel):
    provider: str
    saved: bool
    last4: str = ""


class ClearPersonalLLMKeyResponse(BaseModel):
    provider: str
    cleared: bool


class LLMSettingsGlobalPayload(BaseModel):
    default_provider: str
    default_models: dict[str, str] = Field(default_factory=dict)


class LLMSettingsUserPayload(BaseModel):
    provider: str
    model: str
    display_label: str = ""
    resolved_model: str = ""
    resolution_source: str = "exact"


class LLMRecommendedModelPayload(BaseModel):
    id: str
    display_label: str
    resolved_model: str
    resolution_source: str = "exact"


class LLMProviderOptionPayload(BaseModel):
    id: str
    label: str
    recommended_models: list[LLMRecommendedModelPayload] = Field(default_factory=list)


class LLMSettingsPayload(BaseModel):
    global_default: LLMSettingsGlobalPayload
    user_default: LLMSettingsUserPayload
    effective: LLMSettingsUserPayload
    providers: list[LLMProviderOptionPayload] = Field(default_factory=list)


class LLMFallbackChainPayload(BaseModel):
    global_default: list[str] = Field(default_factory=list)
    user_default: list[str] = Field(default_factory=list)
    effective: list[str] = Field(default_factory=list)
    source: str = "global"


class LLMFallbackChainUpdateRequest(BaseModel):
    chain: list[str] = Field(default_factory=list)
    scope: str = "user"


class LLMSettingsUpdateRequest(BaseModel):
    provider: str | None = None
    model: str | None = None
    scope: str = "user"


class CreateSharedKeyInviteRequest(BaseModel):
    ttl_hours: int = 24
    max_uses: int = 1
    note: str | None = None


class SharedKeyInviteResponse(BaseModel):
    token: str
    invite_url: str
    expires_at: str
    max_uses: int
    note: str | None = None


class RedeemSharedKeyInviteRequest(BaseModel):
    token: str


class RedeemSharedKeyInviteResponse(BaseModel):
    granted: bool
    can_use_shared_key: bool


class CreateRegistrationInviteRequest(BaseModel):
    ttl_hours: int = 24 * 7
    max_uses: int = 20
    note: str | None = None


class CreateRegistrationInviteBatchRequest(BaseModel):
    count: int = 10
    ttl_hours: int = 24 * 7
    max_uses: int = 20
    note: str | None = None


class RegistrationInvitePayload(BaseModel):
    code: str
    invite_url: str
    expires_at: str
    max_uses: int
    used_count: int
    remaining_uses: int
    revoked: bool = False
    note: str | None = None
    created_at: str


class RegistrationInviteListResponse(BaseModel):
    items: list[RegistrationInvitePayload] = Field(default_factory=list)


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str | None = None
    email_code: str | None = None
    invite_code: str | None = None


class RegisterEmailCodeRequest(BaseModel):
    email: str


class RegisterEmailCodeResponse(BaseModel):
    sent: bool
    masked_email: str
    ttl_minutes: int
    resend_after_seconds: int
