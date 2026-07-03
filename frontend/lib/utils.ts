import type {
    AuthPolicy,
    ChatHistoryItem,
    PromptConfig,
    UserPayload,
    ChatTurn,
    PermissionDraft,
    UserEditDraft,
    PromptInstructionKey,
} from "./types";

export const FALLBACK_AUTH_POLICY: AuthPolicy = {
    username: {
        pattern: "^[a-z][a-z0-9_.-]{2,31}$",
        min_length: 3,
        max_length: 32,
        normalization: "trim + lowercase",
        description: "3-32 位，字母开头，仅允许字母/数字/._-",
    },
    password: {
        min_length: 8,
        max_length: 128,
        require_letters: true,
        require_numbers: true,
        forbid_whitespace: true,
        description: "8-128 位，必须包含字母和数字，且不能有空格",
    },
    registration: {
        mode: "open",
        invite_required: false,
        enabled: true,
    },
};

export const SESSION_KEY = "study-assistant:session";

export const EMPTY_PROMPT: PromptConfig = {
    agent_a_instruction: "",
    agent_b_instruction: "",
    agent_c_instruction: "",
    chat_instruction: "",
    formula_instruction: "",
};

export const EMPTY_PERMISSIONS: PermissionDraft = {
    can_manage_accounts: false,
    can_manage_prompts: false,
    can_manage_shared_keys: false,
};

export const PROMPT_FIELD_META: Array<{ key: PromptInstructionKey; label: string }> = [
    { key: "agent_a_instruction", label: "Agent A（文档综述与分组）" },
    { key: "agent_b_instruction", label: "Agent B（分组总结）" },
    { key: "agent_c_instruction", label: "Agent C（逐页解释）" },
    { key: "chat_instruction", label: "Chat（页内追问）" },
    { key: "formula_instruction", label: "Formula（公式识别）" },
];

export function formatIsoTime(iso: string): string {
    const parsed = Date.parse(iso);
    if (Number.isNaN(parsed)) return iso;
    return new Date(parsed).toLocaleString();
}

export function normalizeUsernameInput(value: string): string {
    return value.trim().toLowerCase();
}

export function validateUsernameByPolicy(policy: AuthPolicy, username: string): string | null {
    const normalized = normalizeUsernameInput(username);
    if (!normalized) return "请输入用户名";
    if (normalized.length < policy.username.min_length || normalized.length > policy.username.max_length) {
        return `用户名长度需为 ${policy.username.min_length}-${policy.username.max_length} 位`;
    }
    let usernameRegex: RegExp;
    try {
        usernameRegex = new RegExp(policy.username.pattern);
    } catch {
        usernameRegex = new RegExp(FALLBACK_AUTH_POLICY.username.pattern);
    }
    if (!usernameRegex.test(normalized)) {
        return policy.username.description || "用户名格式不符合要求";
    }
    return null;
}

export function validatePasswordByPolicy(policy: AuthPolicy, password: string): string | null {
    const raw = String(password ?? "");
    if (!raw) return "请输入密码";
    if (raw.length < policy.password.min_length || raw.length > policy.password.max_length) {
        return `密码长度需为 ${policy.password.min_length}-${policy.password.max_length} 位`;
    }
    if (policy.password.forbid_whitespace && /\s/.test(raw)) return "密码不能包含空格";
    if (policy.password.require_letters && !/[A-Za-z]/.test(raw)) return "密码必须包含字母";
    if (policy.password.require_numbers && !/[0-9]/.test(raw)) return "密码必须包含数字";
    return null;
}

export function isPageNotReadyError(message: string): boolean {
    const lower = message.toLowerCase();
    return lower.includes("404") && lower.includes("page not found");
}

export function extractRetryAfterSeconds(message: string): number | null {
    const waitEn = message.match(/wait\s+(\d+)\s*s/i);
    if (waitEn?.[1]) {
        const value = Number(waitEn[1]);
        return Number.isFinite(value) && value > 0 ? Math.floor(value) : null;
    }
    const waitZh = message.match(/等待\s*(\d+)\s*秒/i);
    if (waitZh?.[1]) {
        const value = Number(waitZh[1]);
        return Number.isFinite(value) && value > 0 ? Math.floor(value) : null;
    }
    return null;
}

export function toReadableBullets(items: string[] | undefined, maxCount: number): string[] {
    const source = Array.isArray(items) ? items : [];
    return source
        .map((item) => String(item ?? "").trim())
        .filter(Boolean)
        .slice(0, maxCount);
}

export function historyToTurns(items: ChatHistoryItem[]): ChatTurn[] {
    const turns: ChatTurn[] = [];
    for (const item of items) {
        turns.push({ role: "ask", text: item.question });
        turns.push({
            role: "answer",
            text: item.answer?.answer ?? "",
            citations: item.answer?.citations ?? [],
            scopePages: item.answer?.scopePages ?? [],
        });
    }
    return turns;
}

export function onlyPromptFields(payload: Partial<PromptConfig>): PromptConfig {
    return {
        agent_a_instruction: String(payload.agent_a_instruction ?? ""),
        agent_b_instruction: String(payload.agent_b_instruction ?? ""),
        agent_c_instruction: String(payload.agent_c_instruction ?? ""),
        chat_instruction: String(payload.chat_instruction ?? ""),
        formula_instruction: String(payload.formula_instruction ?? ""),
    };
}

export function samePromptFields(a: Partial<PromptConfig>, b: Partial<PromptConfig>): boolean {
    return (
        String(a.agent_a_instruction ?? "") === String(b.agent_a_instruction ?? "") &&
        String(a.agent_b_instruction ?? "") === String(b.agent_b_instruction ?? "") &&
        String(a.agent_c_instruction ?? "") === String(b.agent_c_instruction ?? "") &&
        String(a.chat_instruction ?? "") === String(b.chat_instruction ?? "") &&
        String(a.formula_instruction ?? "") === String(b.formula_instruction ?? "")
    );
}

export function normalizePermissions(user: Pick<UserPayload, "role" | "permissions"> | null | undefined): PermissionDraft {
    if (!user) return { ...EMPTY_PERMISSIONS };
    if (user.role === "admin") {
        return {
            can_manage_accounts: true,
            can_manage_prompts: true,
            can_manage_shared_keys: true,
        };
    }
    const p = user.permissions;
    return {
        can_manage_accounts: Boolean(p?.can_manage_accounts),
        can_manage_prompts: Boolean(p?.can_manage_prompts),
        can_manage_shared_keys: Boolean(p?.can_manage_shared_keys),
    };
}

export function toUserEditDraft(user: UserPayload): UserEditDraft {
    const role = user.role === "admin" ? "admin" : "user";
    return {
        role,
        is_active: Boolean(user.is_active),
        can_use_shared_key: Boolean(user.can_use_shared_key),
        permissions: normalizePermissions({ role, permissions: user.permissions }),
    };
}
