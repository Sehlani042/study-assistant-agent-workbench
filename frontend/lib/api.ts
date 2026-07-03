import type {
  AuthPolicy,
  ChatAnswer,
  ChatHistoryResponse,
  DocumentRunDetail,
  DocumentRunListResponse,
  DocumentPromptSnapshot,
  DocumentHistoryResponse,
  DocumentStatus,
  ExplanationPreviewResponse,
  LLMAccess,
  LLMFallbackChainSettings,
  LLMSettings,
  LearningPreferences,
  LoginResponse,
  Outline,
  PagePayload,
  PromptConfig,
  RegistrationInvite,
  RegistrationInviteListResponse,
  SharedKeyInvite,
  UserPayload,
} from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
const API_BASE_FALLBACK = process.env.NEXT_PUBLIC_API_BASE_FALLBACK ?? "";
const UPLOAD_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_UPLOAD_TIMEOUT_MS ?? "90000");
const FILE_PROBE_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_FILE_PROBE_TIMEOUT_MS ?? "15000");
const AUTH_TOKEN_KEY = "study-assistant:auth-token";

type PromptOverridePayload = Partial<
  Pick<
    PromptConfig,
    "agent_a_instruction" | "agent_b_instruction" | "agent_c_instruction" | "chat_instruction" | "formula_instruction"
  >
>;

export function assetUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return `${API_BASE}${path}`;
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(AUTH_TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(AUTH_TOKEN_KEY);
}

function withAuthHeaders(headers?: HeadersInit): HeadersInit {
  const token = getAuthToken();
  if (!token) return headers ?? {};
  return {
    ...(headers ?? {}),
    Authorization: `Bearer ${token}`,
  };
}

async function authedFetch(input: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, {
    ...(init ?? {}),
    headers: withAuthHeaders(init?.headers),
  });
  if (response.status === 401) {
    clearAuthToken();
  }
  return response;
}

function isNetworkLoadFailedError(err: unknown): boolean {
  const message = String((err as Error | undefined)?.message ?? err ?? "").toLowerCase();
  return (
    message.includes("load failed") ||
    message.includes("failed to fetch") ||
    message.includes("networkerror")
  );
}

function isAbortError(err: unknown): boolean {
  if (!err) return false;
  if (typeof DOMException !== "undefined" && err instanceof DOMException && err.name === "AbortError") {
    return true;
  }
  const name = String((err as { name?: unknown }).name ?? "");
  const message = String((err as Error | undefined)?.message ?? err ?? "").toLowerCase();
  return name === "AbortError" || message.includes("aborted") || message.includes("aborterror");
}

async function ensureFileReadable(file: File): Promise<void> {
  if (!file) {
    throw new Error("上传失败：未选择文件。");
  }
  if (file.size <= 0) {
    throw new Error("上传失败：文件为空，无法上传。");
  }

  const timeoutMs = Number.isFinite(FILE_PROBE_TIMEOUT_MS) && FILE_PROBE_TIMEOUT_MS > 0 ? FILE_PROBE_TIMEOUT_MS : 15000;
  const chunk = Math.min(file.size, 64 * 1024);
  const probeRanges: Array<[number, number]> = [[0, chunk]];
  if (file.size > chunk * 2) {
    const midStart = Math.max(0, Math.floor(file.size / 2) - Math.floor(chunk / 2));
    probeRanges.push([midStart, Math.min(file.size, midStart + chunk)]);
  }
  if (file.size > chunk) {
    probeRanges.push([Math.max(0, file.size - chunk), file.size]);
  }

  const seen = new Set<string>();
  const uniqueProbeRanges = probeRanges.filter(([start, end]) => {
    const key = `${start}:${end}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  const readSliceWithTimeout = async (start: number, end: number): Promise<void> => {
    const readPromise = file.slice(start, end).arrayBuffer();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const timeoutPromise = new Promise<ArrayBuffer>((_, reject) => {
      timer = setTimeout(() => {
        reject(new Error("file_probe_timeout"));
      }, timeoutMs);
    });
    try {
      await Promise.race([readPromise, timeoutPromise]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  };

  try {
    for (const [start, end] of uniqueProbeRanges) {
      await readSliceWithTimeout(start, end);
    }
  } catch (err) {
    const message = String((err as Error | undefined)?.message ?? err ?? "");
    if (message === "file_probe_timeout") {
      throw new Error("读取文件超时：文件可能仍在云盘占位状态。请先在本地打开/下载完成后再上传。");
    }
    throw new Error("无法读取完整文件：请重新选择本地文件后再上传。");
  }
}

async function authedFetchWithRetry(input: string, init: RequestInit, retries = 1): Promise<Response> {
  let lastErr: unknown = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await authedFetch(input, init);
    } catch (err) {
      lastErr = err;
      if (!isNetworkLoadFailedError(err) || attempt >= retries) {
        throw err;
      }
      await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error("network request failed");
}

async function httpError(response: Response): Promise<Error> {
  const fallback = `HTTP ${response.status}`;
  let bodyText = "";
  try {
    bodyText = await response.text();
  } catch {
    return new Error(fallback);
  }
  if (!bodyText) {
    return new Error(fallback);
  }

  try {
    const parsed = JSON.parse(bodyText) as { detail?: unknown; message?: unknown };
    const detail = String(parsed.detail ?? parsed.message ?? "").trim();
    if (detail) {
      return new Error(`HTTP ${response.status}: ${detail}`);
    }
  } catch {
    // not json
  }
  return new Error(`HTTP ${response.status}: ${bodyText}`);
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  const payload = (await response.json()) as LoginResponse;
  setAuthToken(payload.access_token);
  return payload;
}

export async function getAuthPolicy(): Promise<AuthPolicy> {
  const response = await fetch(`${API_BASE}/api/v1/auth/policy`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function registerUser(payload: {
  username: string;
  password: string;
  email?: string;
  email_code?: string;
  invite_code?: string;
}): Promise<UserPayload> {
  const response = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function sendRegisterEmailCode(
  email: string,
): Promise<{ sent: boolean; masked_email: string; ttl_minutes: number; resend_after_seconds: number }> {
  const response = await fetch(`${API_BASE}/api/v1/auth/register/email-code`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getLLMAccess(): Promise<LLMAccess> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/llm/access`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function setPersonalLLMKey(provider: string, apiKey: string): Promise<{ provider: string; saved: boolean; last4: string }> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/llm/key`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function clearPersonalLLMKey(provider: string): Promise<{ provider: string; cleared: boolean }> {
  const params = new URLSearchParams();
  params.set("provider", provider);
  const response = await authedFetch(`${API_BASE}/api/v1/auth/llm/key?${params.toString()}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getLLMSettings(): Promise<LLMSettings> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/llm`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function updateLLMSettings(payload: {
  provider?: string;
  model?: string;
  scope?: "user" | "global" | "both";
}): Promise<LLMSettings> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/llm`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getLLMFallbackChainSettings(): Promise<LLMFallbackChainSettings> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/llm/fallback-chain`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function updateLLMFallbackChainSettings(payload: {
  chain: string[];
  scope?: "user" | "global" | "both";
}): Promise<LLMFallbackChainSettings> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/llm/fallback-chain`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function createSharedKeyInvite(payload: {
  ttl_hours: number;
  max_uses: number;
  note?: string;
}): Promise<SharedKeyInvite> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/shared-key-invites`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function redeemSharedKeyInvite(token: string): Promise<{ granted: boolean; can_use_shared_key: boolean }> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/shared-key-invites/redeem`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ token }),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function createRegistrationInvite(payload: {
  ttl_hours?: number;
  max_uses?: number;
  note?: string;
}): Promise<RegistrationInvite> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/registration-invites`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function createRegistrationInviteBatch(payload: {
  count: number;
  ttl_hours?: number;
  max_uses?: number;
  note?: string;
}): Promise<RegistrationInviteListResponse> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/registration-invites/batch`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function listRegistrationInvites(limit = 500): Promise<RegistrationInviteListResponse> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/registration-invites?limit=${limit}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function revokeRegistrationInvite(code: string): Promise<RegistrationInvite> {
  const normalized = encodeURIComponent(String(code || "").trim());
  const response = await authedFetch(`${API_BASE}/api/v1/auth/registration-invites/${normalized}/revoke`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function exportRegistrationInvitesCsv(limit = 2000): Promise<string> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/registration-invites/export?limit=${limit}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.text();
}

export async function getCurrentUser(): Promise<UserPayload> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/me`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function logout(): Promise<void> {
  await authedFetch(`${API_BASE}/api/v1/auth/logout`, { method: "POST" });
  clearAuthToken();
}

export async function listUsers(): Promise<UserPayload[]> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/users`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function createUser(payload: {
  username: string;
  password: string;
  role?: "admin" | "user";
  is_active?: boolean;
  can_use_shared_key?: boolean;
  permissions?: {
    can_manage_accounts?: boolean;
    can_manage_prompts?: boolean;
    can_manage_shared_keys?: boolean;
  };
}): Promise<UserPayload> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/users`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function updateUser(
  userId: string,
  payload: {
    role?: "admin" | "user";
    is_active?: boolean;
    can_use_shared_key?: boolean;
    permissions?: {
      can_manage_accounts?: boolean;
      can_manage_prompts?: boolean;
      can_manage_shared_keys?: boolean;
    };
  },
): Promise<UserPayload> {
  const response = await authedFetch(`${API_BASE}/api/v1/auth/users/${userId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function uploadDocument(
  file: File,
  uploadOptions?: {
    prompt_profile?: "default" | "personal";
    task_prompt?: string;
    prompt_overrides?: PromptOverridePayload;
    learning_profile?: LearningPreferences;
    llm_provider?: string;
    llm_model?: string;
  },
): Promise<{ document_id: string; job_id: string }> {
  await ensureFileReadable(file);

  const formData = new FormData();
  formData.append("file", file);
  if (uploadOptions?.prompt_profile) {
    formData.append("prompt_profile", uploadOptions.prompt_profile);
  }
  if (uploadOptions?.task_prompt) {
    formData.append("task_prompt", uploadOptions.task_prompt);
  }
  if (uploadOptions?.prompt_overrides) {
    for (const [key, value] of Object.entries(uploadOptions.prompt_overrides)) {
      const text = String(value ?? "").trim();
      if (text) {
        formData.append(key, text);
      }
    }
  }
  if (uploadOptions?.learning_profile) {
    formData.append("learner_level", uploadOptions.learning_profile.learner_level);
    formData.append("learning_goal", uploadOptions.learning_profile.learning_goal);
    formData.append("depth_mode", uploadOptions.learning_profile.depth_mode);
    formData.append("attention_support", uploadOptions.learning_profile.attention_support);
  }
  if (uploadOptions?.llm_provider) {
    formData.append("llm_provider", uploadOptions.llm_provider);
  }
  if (uploadOptions?.llm_model) {
    formData.append("llm_model", uploadOptions.llm_model);
  }

  const uploadBases = Array.from(new Set([API_BASE, API_BASE_FALLBACK].filter((v) => Boolean(v?.trim()))));
  let networkErr: unknown = null;

  for (let i = 0; i < uploadBases.length; i += 1) {
    const base = uploadBases[i]!;
    const controller = new AbortController();
    const timeoutMs = Number.isFinite(UPLOAD_TIMEOUT_MS) && UPLOAD_TIMEOUT_MS > 0 ? UPLOAD_TIMEOUT_MS : 180000;
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await authedFetchWithRetry(
        `${base}/api/v1/documents`,
        {
          method: "POST",
          body: formData,
          signal: controller.signal,
        },
        1,
      );
      if (!response.ok) {
        throw await httpError(response);
      }
      return response.json();
    } catch (err) {
      if (isAbortError(err)) {
        throw new Error("上传超时：网络较慢或连接不稳定，请重试（建议文件先下载到本地后上传）。");
      }
      if (err instanceof Error && err.message.startsWith("HTTP 400")) {
        throw new Error(
          "上传失败：文件体未被网关完整接收（HTTP 400）。请先将文件下载到本地磁盘后重选上传；若仍失败，请更换浏览器重试。",
        );
      }
      const canTryNextBase = i < uploadBases.length - 1;
      if (isNetworkLoadFailedError(err) && canTryNextBase) {
        networkErr = err;
        continue;
      }
      if (isNetworkLoadFailedError(err)) {
        throw new Error(
          "上传失败：网络连接中断（Load failed）。请重新选择文件后重试；若使用 iPhone/iPad，请先将文件下载到本地再上传。",
        );
      }
      throw err instanceof Error ? err : new Error("上传失败");
    } finally {
      clearTimeout(timer);
    }
  }

  if (isNetworkLoadFailedError(networkErr)) {
    throw new Error("上传失败：网络连接中断（Load failed）。请重新选择文件后重试；若使用 iPhone/iPad，请先将文件下载到本地再上传。");
  }
  throw new Error("上传失败");
}

export async function getDocumentStatus(documentId: string): Promise<DocumentStatus> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getDocumentPromptSnapshot(documentId: string): Promise<DocumentPromptSnapshot> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/prompt`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function listDocuments(limit = 100, view: "active" | "library" | "all" = "all"): Promise<DocumentHistoryResponse> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents?limit=${limit}&view=${view}`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function listDocumentRuns(documentId: string): Promise<DocumentRunListResponse> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/runs`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getDocumentRun(documentId: string, runId: string): Promise<DocumentRunDetail> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/runs/${runId}`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getOutline(documentId: string): Promise<Outline> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/outline`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getPage(
  documentId: string,
  pageNo: number,
  language: "zh" | "en",
  options?: { signal?: AbortSignal },
): Promise<PagePayload> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/pages/${pageNo}?language=${language}`, {
    cache: "no-store",
    signal: options?.signal,
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function regeneratePage(
  documentId: string,
  pageNo: number,
  language: "zh" | "en",
  options?: {
    prompt_profile?: "default" | "personal";
    task_prompt?: string;
    prompt_overrides?: PromptOverridePayload;
    learning_profile?: LearningPreferences;
    llm_provider?: string;
    llm_model?: string;
  },
): Promise<{ run_id: string; page: PagePayload }> {
  const params = new URLSearchParams();
  params.set("language", language);
  if (options?.prompt_profile) {
    params.set("prompt_profile", options.prompt_profile);
  }
  if (typeof options?.task_prompt === "string") {
    params.set("task_prompt", options.task_prompt);
  }
  if (options?.prompt_overrides) {
    for (const [key, value] of Object.entries(options.prompt_overrides)) {
      const text = String(value ?? "").trim();
      if (text) {
        params.set(key, text);
      }
    }
  }
  if (options?.learning_profile) {
    params.set("learner_level", options.learning_profile.learner_level);
    params.set("learning_goal", options.learning_profile.learning_goal);
    params.set("depth_mode", options.learning_profile.depth_mode);
    params.set("attention_support", options.learning_profile.attention_support);
  }
  if (options?.llm_provider) {
    params.set("llm_provider", options.llm_provider);
  }
  if (options?.llm_model) {
    params.set("llm_model", options.llm_model);
  }

  const regen = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/pages/${pageNo}/regenerate?${params.toString()}`, {
    method: "POST",
  });
  if (!regen.ok) {
    throw await httpError(regen);
  }
  const regenBody = await regen.json() as { run_id: string };
  const page = await getPage(documentId, pageNo, language);
  return { run_id: regenBody.run_id, page };
}

export async function explainPage(
  documentId: string,
  pageNo: number,
  language: "zh" | "en",
  options?: {
    prompt_profile?: "default" | "personal";
    task_prompt?: string;
    prompt_overrides?: PromptOverridePayload;
    learning_profile?: LearningPreferences;
    llm_provider?: string;
    llm_model?: string;
  },
): Promise<{ run_id: string; page: PagePayload }> {
  const params = new URLSearchParams();
  params.set("language", language);
  if (options?.prompt_profile) {
    params.set("prompt_profile", options.prompt_profile);
  }
  if (typeof options?.task_prompt === "string") {
    params.set("task_prompt", options.task_prompt);
  }
  if (options?.prompt_overrides) {
    for (const [key, value] of Object.entries(options.prompt_overrides)) {
      const text = String(value ?? "").trim();
      if (text) {
        params.set(key, text);
      }
    }
  }
  if (options?.learning_profile) {
    params.set("learner_level", options.learning_profile.learner_level);
    params.set("learning_goal", options.learning_profile.learning_goal);
    params.set("depth_mode", options.learning_profile.depth_mode);
    params.set("attention_support", options.learning_profile.attention_support);
  }
  if (options?.llm_provider) {
    params.set("llm_provider", options.llm_provider);
  }
  if (options?.llm_model) {
    params.set("llm_model", options.llm_model);
  }

  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/pages/${pageNo}/explain?${params.toString()}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  const body = await response.json() as { run_id: string };
  const page = await getPage(documentId, pageNo, language);
  return { run_id: body.run_id, page };
}

export async function getLearningPreferences(): Promise<LearningPreferences> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/learning`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function updateLearningPreferences(payload: Partial<LearningPreferences>): Promise<LearningPreferences> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/learning`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function previewExplanation(payload: {
  page_text: string;
  formulas?: { latex: string; sourceSpan?: string }[];
  language: "zh" | "en";
  prompt_profile?: "default" | "personal";
  task_prompt?: string;
  prompt_overrides?: PromptOverridePayload;
  learning_profile: LearningPreferences;
  llm_provider?: string;
  llm_model?: string;
}): Promise<ExplanationPreviewResponse> {
  const response = await authedFetch(`${API_BASE}/api/v1/lab/explanation-preview`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      page_text: payload.page_text,
      formulas: payload.formulas ?? [],
      language: payload.language,
      prompt_profile: payload.prompt_profile ?? "personal",
      task_prompt: payload.task_prompt,
      prompt_overrides: payload.prompt_overrides ?? {},
      learner_level: payload.learning_profile.learner_level,
      learning_goal: payload.learning_profile.learning_goal,
      depth_mode: payload.learning_profile.depth_mode,
      attention_support: payload.learning_profile.attention_support,
      llm_provider: payload.llm_provider,
      llm_model: payload.llm_model,
    }),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function chatOnPage(
  documentId: string,
  pageNo: number,
  question: string,
  language: "zh" | "en",
): Promise<ChatAnswer> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/pages/${pageNo}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ question, language }),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  const data = await response.json();
  return data.answer;
}

export async function getChatHistory(documentId: string, pageNo: number, limit = 100): Promise<ChatHistoryResponse> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/pages/${pageNo}/chat?limit=${limit}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getPromptConfig(): Promise<PromptConfig> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/prompt`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function updatePromptConfig(payload: Partial<PromptConfig>): Promise<PromptConfig> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/prompt`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function resetPromptConfig(): Promise<PromptConfig> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/prompt/reset`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function getDefaultPromptConfig(): Promise<PromptConfig> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/prompt/default`, { cache: "no-store" });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function updateDefaultPromptConfig(payload: Partial<PromptConfig>): Promise<PromptConfig> {
  const response = await authedFetch(`${API_BASE}/api/v1/settings/prompt/default`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}

export async function clearDocument(documentId: string): Promise<void> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/clear`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
}

export async function cancelDocument(documentId: string): Promise<{ document_id: string; canceled: boolean; status: string }> {
  const response = await authedFetch(`${API_BASE}/api/v1/documents/${documentId}/cancel`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await httpError(response);
  }
  return response.json();
}
