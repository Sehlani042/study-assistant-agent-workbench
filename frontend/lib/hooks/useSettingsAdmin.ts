import { useState, useCallback, useMemo, useEffect } from "react";
import {
    getLLMAccess,
    getLLMFallbackChainSettings,
    getLLMSettings,
    getLearningPreferences,
    getPromptConfig,
    listUsers,
    getDefaultPromptConfig,
    getDocumentPromptSnapshot,
    createUser,
    updateUser,
    setPersonalLLMKey,
    clearPersonalLLMKey,
    createSharedKeyInvite,
    createRegistrationInviteBatch,
    redeemSharedKeyInvite,
    listRegistrationInvites,
    revokeRegistrationInvite,
    exportRegistrationInvitesCsv,
    updatePromptConfig,
    resetPromptConfig,
    updateDefaultPromptConfig,
    getCurrentUser,
    updateLLMSettings,
    updateLearningPreferences,
    updateLLMFallbackChainSettings,
} from "@/lib/api";
import type {
    LLMAccess,
    LLMFallbackChainSettings,
    LLMProviderOption,
    LLMSettings,
    LearningPreferences,
    PromptConfig,
    PromptOverridePayload,
    DocumentPromptSnapshot,
    UserPayload,
    PermissionDraft,
    UserEditDraft,
    SharedKeyInvite,
    RegistrationInvite,
    AuthPolicy,
} from "@/lib/types";
import {
    EMPTY_PROMPT,
    EMPTY_PERMISSIONS,
    PROMPT_FIELD_META,
    normalizePermissions,
    toUserEditDraft,
    validateUsernameByPolicy,
    validatePasswordByPolicy,
    normalizeUsernameInput,
} from "@/lib/utils";

export function useSettingsAdmin({
    currentUser,
    setCurrentUser,
    canManageAccounts,
    canManagePrompts,
    canManageSharedKeys,
    authPolicy,
}: {
    currentUser: UserPayload | null;
    setCurrentUser: (user: UserPayload | null) => void;
    canManageAccounts: boolean;
    canManagePrompts: boolean;
    canManageSharedKeys: boolean;
    authPolicy: AuthPolicy;
}) {
    const [llmAccess, setLlmAccess] = useState<LLMAccess | null>(null);
    const [llmSettings, setLlmSettings] = useState<LLMSettings | null>(null);
    const [learningPreferences, setLearningPreferences] = useState<LearningPreferences>({
        learner_level: "beginner",
        learning_goal: "understand",
        depth_mode: "standard",
        attention_support: "adhd_friendly",
        source: "default",
    });
    const [isSavingLearningPreferences, setIsSavingLearningPreferences] = useState(false);
    const [personalApiKey, setPersonalApiKey] = useState("");
    const [userProviderDraft, setUserProviderDraft] = useState("gemini");
    const [userModelDraft, setUserModelDraft] = useState("");
    const [globalProviderDraft, setGlobalProviderDraft] = useState("gemini");
    const [globalModelDraft, setGlobalModelDraft] = useState("");
    const [isSavingLLMUserDefault, setIsSavingLLMUserDefault] = useState(false);
    const [isSavingLLMGlobalDefault, setIsSavingLLMGlobalDefault] = useState(false);
    const [isSavingPersonalKey, setIsSavingPersonalKey] = useState(false);
    const [isClearingPersonalKey, setIsClearingPersonalKey] = useState(false);
    const [fallbackChainSettings, setFallbackChainSettings] = useState<LLMFallbackChainSettings | null>(null);
    const [userFallbackChainDraft, setUserFallbackChainDraft] = useState("gemini:flash, gemini:pro, openai:gpt-5.2-mini");
    const [globalFallbackChainDraft, setGlobalFallbackChainDraft] = useState("gemini:flash, gemini:pro, openai:gpt-5.2-mini");
    const [isSavingUserFallbackChain, setIsSavingUserFallbackChain] = useState(false);
    const [isSavingGlobalFallbackChain, setIsSavingGlobalFallbackChain] = useState(false);

    const [isSavingPrompt, setIsSavingPrompt] = useState(false);
    const [isSavingDefaultPrompt, setIsSavingDefaultPrompt] = useState(false);

    const [promptDraft, setPromptDraft] = useState<PromptConfig>(EMPTY_PROMPT);
    const [defaultPromptDraft, setDefaultPromptDraft] = useState<PromptConfig>(EMPTY_PROMPT);
    const [promptSource, setPromptSource] = useState<"default" | "personal">("default");
    const [hasCustomPrompt, setHasCustomPrompt] = useState(false);
    const [taskPromptProfile, setTaskPromptProfile] = useState<"default" | "personal">("personal");
    const [taskPrompt, setTaskPrompt] = useState("");
    const [confirmPromptBeforeRun, setConfirmPromptBeforeRun] = useState(true);
    const [runPromptOverrides, setRunPromptOverrides] = useState<PromptOverridePayload>({});
    const [documentPromptSnapshot, setDocumentPromptSnapshot] = useState<DocumentPromptSnapshot | null>(null);

    const [userList, setUserList] = useState<UserPayload[]>([]);
    const [newUsername, setNewUsername] = useState("");
    const [newPassword, setNewPassword] = useState("");
    const [newRole, setNewRole] = useState<"admin" | "user">("user");
    const [newIsActive, setNewIsActive] = useState(true);
    const [newCanUseSharedKey, setNewCanUseSharedKey] = useState(false);
    const [newPermissions, setNewPermissions] = useState<PermissionDraft>({ ...EMPTY_PERMISSIONS });
    const [userEdits, setUserEdits] = useState<Record<string, UserEditDraft>>({});
    const [savingUserId, setSavingUserId] = useState<string | null>(null);
    const [isCreatingUser, setIsCreatingUser] = useState(false);

    const [inviteTtlHours, setInviteTtlHours] = useState(24);
    const [inviteMaxUses, setInviteMaxUses] = useState(1);
    const [inviteNote, setInviteNote] = useState("");
    const [generatedInvite, setGeneratedInvite] = useState<SharedKeyInvite | null>(null);
    const [isCreatingInvite, setIsCreatingInvite] = useState(false);
    const [redeemInviteToken, setRedeemInviteToken] = useState("");
    const [isRedeemingInvite, setIsRedeemingInvite] = useState(false);

    const [registrationInviteTtlHours, setRegistrationInviteTtlHours] = useState(24 * 7);
    const [registrationInviteMaxUses, setRegistrationInviteMaxUses] = useState(20);
    const [registrationInviteBatchCount, setRegistrationInviteBatchCount] = useState(5);
    const [registrationInviteNote, setRegistrationInviteNote] = useState("");
    const [registrationInvites, setRegistrationInvites] = useState<RegistrationInvite[]>([]);
    const [isCreatingRegistrationInvites, setIsCreatingRegistrationInvites] = useState(false);
    const [isRefreshingRegistrationInvites, setIsRefreshingRegistrationInvites] = useState(false);
    const [isExportingRegistrationInvites, setIsExportingRegistrationInvites] = useState(false);
    const [revokingRegistrationInviteCode, setRevokingRegistrationInviteCode] = useState<string | null>(null);

    const [error, setError] = useState<string | null>(null);

    const createUsernameError = useMemo(() => validateUsernameByPolicy(authPolicy, newUsername), [authPolicy, newUsername]);
    const createPasswordError = useMemo(() => validatePasswordByPolicy(authPolicy, newPassword), [authPolicy, newPassword]);

    const effectiveProvider = String(llmSettings?.effective?.provider || llmAccess?.provider || "gemini");
    const effectiveModel = String(llmSettings?.effective?.model || llmAccess?.model || "");
    const providerOptions = useMemo<LLMProviderOption[]>(() => {
        if (Array.isArray(llmSettings?.providers) && llmSettings.providers.length > 0) {
            return llmSettings.providers;
        }
        return [
            { id: "gemini", label: "Google Gemini", recommended_models: [] },
            { id: "openai", label: "OpenAI", recommended_models: [] },
            { id: "mock", label: "Mock", recommended_models: [] },
        ];
    }, [llmSettings]);
    const providerOptionMap = useMemo(
        () => Object.fromEntries(providerOptions.map((item) => [item.id, item])),
        [providerOptions],
    );
    const effectiveDisplayLabel = String(
        llmSettings?.effective?.display_label
        || llmAccess?.display_label
        || llmAccess?.providers?.[effectiveProvider]?.display_label
        || effectiveModel
        || effectiveProvider,
    );
    const effectiveResolvedModel = String(
        llmSettings?.effective?.resolved_model
        || llmAccess?.resolved_model
        || llmAccess?.providers?.[effectiveProvider]?.resolved_model
        || effectiveModel,
    );
    const effectiveResolutionSource = String(
        llmSettings?.effective?.resolution_source
        || llmAccess?.resolution_source
        || llmAccess?.providers?.[effectiveProvider]?.resolution_source
        || "exact",
    );
    const providerAccess = llmAccess?.providers?.[effectiveProvider];
    const llmNeedsPersonalKey = Boolean(
        (providerAccess?.requires_personal_key ?? llmAccess?.requires_personal_key) &&
        !(providerAccess?.has_personal_key ?? llmAccess?.has_personal_key),
    );
    const llmSummaryText = useMemo(() => {
        if (!llmAccess) return "";
        const canUseSharedKey = providerAccess?.can_use_shared_key ?? llmAccess.can_use_shared_key;
        const hasPersonalKey = providerAccess?.has_personal_key ?? llmAccess.has_personal_key;
        const providerLabel = effectiveResolvedModel && effectiveResolvedModel !== effectiveDisplayLabel
            ? `${effectiveDisplayLabel} → ${effectiveResolvedModel}`
            : effectiveDisplayLabel;
        if (canUseSharedKey) return `模型：${providerLabel} · 使用平台共享 Key`;
        if (llmNeedsPersonalKey && hasPersonalKey) return `模型：${providerLabel} · 使用个人 Key`;
        if (llmNeedsPersonalKey) return `模型：${providerLabel} · 需要先配置个人 Key`;
        return `模型：${providerLabel}`;
    }, [effectiveDisplayLabel, effectiveResolvedModel, llmAccess, llmNeedsPersonalKey, providerAccess]);

    const runBasePrompt = useMemo(() => {
        const base = taskPromptProfile === "default" ? defaultPromptDraft : promptDraft;
        const cleanTask = taskPrompt.trim();
        const out = { ...base };
        if (!cleanTask) return out;
        for (const field of PROMPT_FIELD_META) {
            const key = field.key;
            const raw = String(out[key] ?? "");
            out[key] = `${raw}\n任务特定补充要求（仅当前文档任务）：${cleanTask}`;
        }
        return out;
    }, [taskPromptProfile, taskPrompt, defaultPromptDraft, promptDraft]);

    const effectiveRunPrompt = useMemo(() => {
        const out = { ...runBasePrompt };
        for (const field of PROMPT_FIELD_META) {
            const override = String(runPromptOverrides[field.key] ?? "").trim();
            if (!override) continue;
            out[field.key] = override;
        }
        return out;
    }, [runBasePrompt, runPromptOverrides]);

    const collectRunPromptOverrides = useCallback((): PromptOverridePayload => {
        const out: PromptOverridePayload = {};
        for (const field of PROMPT_FIELD_META) {
            const key = field.key;
            const override = String(runPromptOverrides[key] ?? "").trim();
            if (!override) continue;
            const baseValue = String(runBasePrompt[key] ?? "").trim();
            if (override !== baseValue) {
                out[key] = override;
            }
        }
        return out;
    }, [runPromptOverrides, runBasePrompt]);

    const loadUsers = useCallback(async () => {
        if (!canManageAccounts) return;
        try {
            const users = await listUsers();
            setUserList(users);
        } catch {
            // Ignore
        }
    }, [canManageAccounts]);

    const refreshRegistrationInvites = useCallback(async (options?: { silent?: boolean }) => {
        if (!canManageAccounts) return;
        if (!options?.silent) {
            setIsRefreshingRegistrationInvites(true);
        }
        try {
            const payload = await listRegistrationInvites(500);
            setRegistrationInvites(payload.items || []);
        } catch (err) {
            if (!options?.silent) {
                setError(err instanceof Error ? err.message : "加载注册邀请码失败");
            }
        } finally {
            if (!options?.silent) {
                setIsRefreshingRegistrationInvites(false);
            }
        }
    }, [canManageAccounts]);

    const refreshLLMAccess = useCallback(async () => {
        try {
            const [accessPayload, settingsPayload] = await Promise.all([getLLMAccess(), getLLMSettings()]);
            setLlmAccess(accessPayload);
            setLlmSettings(settingsPayload);
            return accessPayload;
        } catch {
            return null;
        }
    }, []);

    const refreshFallbackChainSettings = useCallback(async () => {
        try {
            const payload = await getLLMFallbackChainSettings();
            setFallbackChainSettings(payload);
            return payload;
        } catch {
            return null;
        }
    }, []);

    const refreshLearningPreferences = useCallback(async () => {
        try {
            const payload = await getLearningPreferences();
            setLearningPreferences(payload);
            return payload;
        } catch {
            return null;
        }
    }, []);

    const ensureLLMReady = useCallback(() => {
        if (!llmAccess) return true;
        const needsKey = providerAccess?.requires_personal_key ?? llmAccess.requires_personal_key;
        const hasKey = providerAccess?.has_personal_key ?? llmAccess.has_personal_key;
        if (needsKey && !hasKey) {
            setError(`当前账号需要先填写 ${effectiveProvider} Key 才能生成解释。`);
            return false;
        }
        return true;
    }, [effectiveProvider, llmAccess, providerAccess]);

    const loadDocumentPromptSnapshot = useCallback(async (docId: string, options?: { silent?: boolean }) => {
        if (!docId) {
            setDocumentPromptSnapshot(null);
            return;
        }
        try {
            const payload = await getDocumentPromptSnapshot(docId);
            setDocumentPromptSnapshot(payload);
        } catch (err) {
            if (!options?.silent) {
                setError(err instanceof Error ? err.message : "加载当前文档 Prompt 失败");
            }
            setDocumentPromptSnapshot(null);
        }
    }, []);

    // Sync users drafts
    useEffect(() => {
        const drafts: Record<string, UserEditDraft> = {};
        for (const user of userList) {
            drafts[user.id] = toUserEditDraft(user);
        }
        setUserEdits(drafts);
    }, [userList]);

    // Sync new permissions when role changes
    useEffect(() => {
        if (newRole === "admin") {
            setNewIsActive(true);
            setNewCanUseSharedKey(true);
            setNewPermissions({
                can_manage_accounts: true,
                can_manage_prompts: true,
                can_manage_shared_keys: true,
            });
        }
    }, [newRole]);

    useEffect(() => {
        if (!llmSettings) return;
        const userProvider = String(llmSettings.user_default?.provider || llmSettings.effective?.provider || "gemini");
        const userModel = String(llmSettings.user_default?.model || llmSettings.effective?.model || "");
        const globalProvider = String(llmSettings.global_default?.default_provider || "gemini");
        const globalModels = llmSettings.global_default?.default_models || {};
        const globalModel = String(globalModels[globalProvider] || "");
        setUserProviderDraft(userProvider);
        setUserModelDraft(userModel);
        setGlobalProviderDraft(globalProvider);
        setGlobalModelDraft(globalModel);
    }, [llmSettings]);

    useEffect(() => {
        if (!fallbackChainSettings) return;
        setUserFallbackChainDraft((fallbackChainSettings.user_default || fallbackChainSettings.effective || []).join(", "));
        setGlobalFallbackChainDraft((fallbackChainSettings.global_default || []).join(", "));
    }, [fallbackChainSettings]);

    // Load configs on login
    useEffect(() => {
        if (!currentUser) return;
        let cancelled = false;

        const run = async () => {
            await Promise.all([refreshLLMAccess(), refreshFallbackChainSettings(), refreshLearningPreferences()]);
            try {
                const cfg = await getPromptConfig();
                if (!cancelled) {
                    setPromptDraft({
                        ...EMPTY_PROMPT,
                        agent_a_instruction: String(cfg.agent_a_instruction ?? ""),
                        agent_b_instruction: String(cfg.agent_b_instruction ?? ""),
                        agent_c_instruction: String(cfg.agent_c_instruction ?? ""),
                        chat_instruction: String(cfg.chat_instruction ?? ""),
                        formula_instruction: String(cfg.formula_instruction ?? ""),
                    });
                    setPromptSource(cfg.source === "personal" ? "personal" : "default");
                    setHasCustomPrompt(Boolean(cfg.has_custom));
                    setTaskPromptProfile(cfg.source === "personal" ? "personal" : "default");
                }
            } catch { }

            if (canManageAccounts) {
                await loadUsers();
                await refreshRegistrationInvites({ silent: true });
            }

            try {
                const defaultCfg = await getDefaultPromptConfig();
                if (!cancelled) {
                    setDefaultPromptDraft({
                        ...EMPTY_PROMPT,
                        agent_a_instruction: String(defaultCfg.agent_a_instruction ?? ""),
                        agent_b_instruction: String(defaultCfg.agent_b_instruction ?? ""),
                        agent_c_instruction: String(defaultCfg.agent_c_instruction ?? ""),
                        chat_instruction: String(defaultCfg.chat_instruction ?? ""),
                        formula_instruction: String(defaultCfg.formula_instruction ?? ""),
                    });
                }
            } catch { }

            try {
                const params = new URLSearchParams(window.location.search);
                const inviteToken = params.get("sharedKeyInvite");
                if (inviteToken && !cancelled) {
                    setRedeemInviteToken(inviteToken);
                }
            } catch { }
        };

        run();
        return () => { cancelled = true; };
    }, [currentUser, canManageAccounts, loadUsers, refreshLLMAccess, refreshRegistrationInvites, refreshFallbackChainSettings, refreshLearningPreferences]);

    const onSaveLearningPreferences = async () => {
        setError(null);
        setIsSavingLearningPreferences(true);
        try {
            const saved = await updateLearningPreferences({
                learner_level: learningPreferences.learner_level,
                learning_goal: learningPreferences.learning_goal,
                depth_mode: learningPreferences.depth_mode,
                attention_support: learningPreferences.attention_support,
            });
            setLearningPreferences(saved);
        } catch (err) {
            setError(err instanceof Error ? err.message : "保存学习参数失败");
        } finally {
            setIsSavingLearningPreferences(false);
        }
    };

    const onCreateUser = async () => {
        if (!canManageAccounts) return;
        const usernameErr = validateUsernameByPolicy(authPolicy, newUsername);
        const passwordErr = validatePasswordByPolicy(authPolicy, newPassword);
        if (usernameErr || passwordErr) {
            setError(usernameErr ?? passwordErr);
            return;
        }
        setError(null);
        setIsCreatingUser(true);
        try {
            const createPayloadPermissions = newRole === "admin"
                ? { can_manage_accounts: true, can_manage_prompts: true, can_manage_shared_keys: true }
                : newPermissions;
            await createUser({
                username: normalizeUsernameInput(newUsername),
                password: newPassword,
                role: newRole,
                is_active: newRole === "admin" ? true : newIsActive,
                can_use_shared_key: newRole === "admin" ? true : newCanUseSharedKey,
                permissions: createPayloadPermissions,
            });
            setNewUsername("");
            setNewPassword("");
            setNewRole("user");
            setNewIsActive(true);
            setNewCanUseSharedKey(false);
            setNewPermissions({ ...EMPTY_PERMISSIONS });
            await loadUsers();
        } catch (err) {
            setError(err instanceof Error ? err.message : "创建账号失败");
        } finally {
            setIsCreatingUser(false);
        }
    };

    const patchUserDraft = (userId: string, updater: (draft: UserEditDraft) => UserEditDraft) => {
        setUserEdits((prev) => {
            const current = prev[userId];
            if (!current) return prev;
            return { ...prev, [userId]: updater(current) };
        });
    };

    const onSaveUserEdit = async (userId: string) => {
        const draft = userEdits[userId];
        if (!draft) return;
        const nextPermissions = draft.role === "admin"
            ? { can_manage_accounts: true, can_manage_prompts: true, can_manage_shared_keys: true }
            : draft.permissions;
        setError(null);
        setSavingUserId(userId);
        try {
            await updateUser(userId, {
                role: draft.role,
                is_active: draft.role === "admin" ? true : draft.is_active,
                can_use_shared_key: draft.role === "admin" ? true : draft.can_use_shared_key,
                permissions: nextPermissions,
            });
            await loadUsers();
            if (currentUser?.id === userId) {
                const me = await getCurrentUser();
                setCurrentUser(me);
                await refreshLLMAccess();
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "更新账号失败");
        } finally {
            setSavingUserId(null);
        }
    };

    const onSavePersonalKey = async () => {
        if (!personalApiKey.trim()) {
            setError(`请输入 ${effectiveProvider} Key`);
            return;
        }
        setError(null);
        setIsSavingPersonalKey(true);
        try {
            await setPersonalLLMKey(effectiveProvider, personalApiKey.trim());
            setPersonalApiKey("");
            await refreshLLMAccess();
        } catch (err) {
            setError(err instanceof Error ? err.message : `保存 ${effectiveProvider} Key 失败`);
        } finally {
            setIsSavingPersonalKey(false);
        }
    };

    const onClearPersonalKey = async () => {
        setError(null);
        setIsClearingPersonalKey(true);
        try {
            await clearPersonalLLMKey(effectiveProvider);
            await refreshLLMAccess();
        } catch (err) {
            setError(err instanceof Error ? err.message : `清除 ${effectiveProvider} Key 失败`);
        } finally {
            setIsClearingPersonalKey(false);
        }
    };

    const onCreateSharedInvite = async () => {
        if (!canManageSharedKeys) return;
        setError(null);
        setIsCreatingInvite(true);
        try {
            const invite = await createSharedKeyInvite({
                ttl_hours: inviteTtlHours,
                max_uses: inviteMaxUses,
                note: inviteNote.trim() || undefined,
            });
            setGeneratedInvite(invite);
        } catch (err) {
            setError(err instanceof Error ? err.message : "创建授权链接失败");
        } finally {
            setIsCreatingInvite(false);
        }
    };

    const onCreateRegistrationInvites = async () => {
        if (!canManageAccounts) return;
        setError(null);
        setIsCreatingRegistrationInvites(true);
        try {
            const payload = await createRegistrationInviteBatch({
                count: registrationInviteBatchCount,
                ttl_hours: registrationInviteTtlHours,
                max_uses: registrationInviteMaxUses,
                note: registrationInviteNote.trim() || undefined,
            });
            const fresh = payload.items || [];
            setRegistrationInvites((prev) => [...fresh, ...prev].slice(0, 500));
        } catch (err) {
            setError(err instanceof Error ? err.message : "生成注册邀请码失败");
        } finally {
            setIsCreatingRegistrationInvites(false);
        }
    };

    const onRevokeRegistrationInvite = async (code: string) => {
        if (!canManageAccounts) return;
        const target = String(code || "").trim().toUpperCase();
        if (!target) return;
        setError(null);
        setRevokingRegistrationInviteCode(target);
        try {
            const updated = await revokeRegistrationInvite(target);
            setRegistrationInvites((prev) => {
                const next = prev.map((item) => (item.code === target ? updated : item));
                if (!next.some((item) => item.code === target)) {
                    next.unshift(updated);
                }
                return next;
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : "撤销注册邀请码失败");
        } finally {
            setRevokingRegistrationInviteCode(null);
        }
    };

    const onExportRegistrationInvites = async () => {
        if (!canManageAccounts) return;
        setError(null);
        setIsExportingRegistrationInvites(true);
        try {
            const csvText = await exportRegistrationInvitesCsv(2000);
            const blob = new Blob([csvText], { type: "text/csv;charset=utf-8;" });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = "registration-invites.csv";
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(url);
        } catch (err) {
            setError(err instanceof Error ? err.message : "导出注册邀请码失败");
        } finally {
            setIsExportingRegistrationInvites(false);
        }
    };

    const onRedeemInvite = async () => {
        const token = redeemInviteToken.trim();
        if (!token) {
            setError("请输入授权 token");
            return;
        }
        setError(null);
        setIsRedeemingInvite(true);
        try {
            await redeemSharedKeyInvite(token);
            await refreshLLMAccess();
            try {
                const url = new URL(window.location.href);
                if (url.searchParams.has("sharedKeyInvite")) {
                    url.searchParams.delete("sharedKeyInvite");
                    window.history.replaceState({}, "", url.toString());
                }
            } catch { }
        } catch (err) {
            setError(err instanceof Error ? err.message : "兑换授权失败");
        } finally {
            setIsRedeemingInvite(false);
        }
    };

    const onSaveUserLLMDefault = async () => {
        const provider = userProviderDraft.trim();
        const model = userModelDraft.trim();
        if (!provider) {
            setError("请先选择默认 Provider");
            return;
        }
        if (!model) {
            setError("请填写默认模型名");
            return;
        }
        setError(null);
        setIsSavingLLMUserDefault(true);
        try {
            const payload = await updateLLMSettings({
                scope: "user",
                provider,
                model,
            });
            setLlmSettings(payload);
            await refreshLLMAccess();
        } catch (err) {
            setError(err instanceof Error ? err.message : "保存个人默认模型失败");
        } finally {
            setIsSavingLLMUserDefault(false);
        }
    };

    const onSaveGlobalLLMDefault = async () => {
        if (!canManageAccounts) {
            setError("当前账号没有全局模型配置权限");
            return;
        }
        const provider = globalProviderDraft.trim();
        const model = globalModelDraft.trim();
        if (!provider) {
            setError("请先选择全局默认 Provider");
            return;
        }
        if (!model) {
            setError("请填写全局默认模型名");
            return;
        }
        setError(null);
        setIsSavingLLMGlobalDefault(true);
        try {
            const payload = await updateLLMSettings({
                scope: "global",
                provider,
                model,
            });
            setLlmSettings(payload);
            await refreshLLMAccess();
        } catch (err) {
            setError(err instanceof Error ? err.message : "保存全局默认模型失败");
        } finally {
            setIsSavingLLMGlobalDefault(false);
        }
    };

    const onSaveUserFallbackChain = async () => {
        const chain = userFallbackChainDraft
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
        if (chain.length <= 0) {
            setError("请至少填写一个兜底链节点，例如 gemini:flash, gemini:pro, openai:gpt-5.2-mini");
            return;
        }
        setError(null);
        setIsSavingUserFallbackChain(true);
        try {
            const payload = await updateLLMFallbackChainSettings({
                scope: "user",
                chain,
            });
            setFallbackChainSettings(payload);
        } catch (err) {
            setError(err instanceof Error ? err.message : "保存个人兜底链失败");
        } finally {
            setIsSavingUserFallbackChain(false);
        }
    };

    const onSaveGlobalFallbackChain = async () => {
        if (!canManageAccounts) {
            setError("当前账号没有全局兜底链管理权限");
            return;
        }
        const chain = globalFallbackChainDraft
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
        if (chain.length <= 0) {
            setError("请至少填写一个兜底链节点");
            return;
        }
        setError(null);
        setIsSavingGlobalFallbackChain(true);
        try {
            const payload = await updateLLMFallbackChainSettings({
                scope: "global",
                chain,
            });
            setFallbackChainSettings(payload);
        } catch (err) {
            setError(err instanceof Error ? err.message : "保存全局兜底链失败");
        } finally {
            setIsSavingGlobalFallbackChain(false);
        }
    };

    const onSavePrompt = async () => {
        setError(null);
        setIsSavingPrompt(true);
        try {
            const saved = await updatePromptConfig(promptDraft);
            setPromptDraft({ ...saved });
            setPromptSource(saved.source === "personal" ? "personal" : "default");
            setHasCustomPrompt(Boolean(saved.has_custom));
        } catch (err) {
            setError(err instanceof Error ? err.message : "保存 Prompt 配置失败");
        } finally {
            setIsSavingPrompt(false);
        }
    };

    const onResetPrompt = async () => {
        setError(null);
        setIsSavingPrompt(true);
        try {
            const reset = await resetPromptConfig();
            setPromptDraft({ ...reset });
            setPromptSource("default");
            setHasCustomPrompt(false);
        } catch (err) {
            setError(err instanceof Error ? err.message : "恢复默认 Prompt 失败");
        } finally {
            setIsSavingPrompt(false);
        }
    };

    const onSaveDefaultPrompt = async () => {
        if (!canManagePrompts) {
            setError("当前账号没有系统 Prompt 管理权限");
            return;
        }
        setError(null);
        setIsSavingDefaultPrompt(true);
        try {
            const saved = await updateDefaultPromptConfig(defaultPromptDraft);
            setDefaultPromptDraft({ ...saved });
            if (!hasCustomPrompt) {
                const cfg = await getPromptConfig();
                setPromptDraft({ ...cfg });
                setPromptSource(cfg.source === "personal" ? "personal" : "default");
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "保存系统默认 Prompt 失败");
        } finally {
            setIsSavingDefaultPrompt(false);
        }
    };

    const onUseSystemDefaultForMyPrompt = () => {
        setPromptDraft({ ...defaultPromptDraft });
        setTaskPromptProfile("default");
    };

    return {
        llmAccess,
        llmSettings,
        effectiveProvider,
        effectiveModel,
        effectiveDisplayLabel,
        effectiveResolvedModel,
        effectiveResolutionSource,
        providerOptions,
        providerOptionMap,
        providerAccess,
        llmNeedsPersonalKey,
        llmSummaryText,
        personalApiKey, setPersonalApiKey,
        userProviderDraft, setUserProviderDraft,
        userModelDraft, setUserModelDraft,
        globalProviderDraft, setGlobalProviderDraft,
        globalModelDraft, setGlobalModelDraft,
        isSavingLLMUserDefault,
        isSavingLLMGlobalDefault,
        isSavingPersonalKey,
        isClearingPersonalKey,
        fallbackChainSettings,
        learningPreferences,
        setLearningPreferences,
        userFallbackChainDraft, setUserFallbackChainDraft,
        globalFallbackChainDraft, setGlobalFallbackChainDraft,
        isSavingUserFallbackChain,
        isSavingGlobalFallbackChain,
        isSavingLearningPreferences,
        isSavingPrompt,
        isSavingDefaultPrompt,
        promptDraft, setPromptDraft,
        defaultPromptDraft, setDefaultPromptDraft,
        promptSource,
        hasCustomPrompt,
        taskPromptProfile, setTaskPromptProfile,
        taskPrompt, setTaskPrompt,
        confirmPromptBeforeRun, setConfirmPromptBeforeRun,
        runPromptOverrides, setRunPromptOverrides,
        documentPromptSnapshot,
        userList,
        newUsername, setNewUsername,
        newPassword, setNewPassword,
        newRole, setNewRole,
        newIsActive, setNewIsActive,
        newCanUseSharedKey, setNewCanUseSharedKey,
        newPermissions, setNewPermissions,
        userEdits, patchUserDraft,
        savingUserId,
        isCreatingUser,
        inviteTtlHours, setInviteTtlHours,
        inviteMaxUses, setInviteMaxUses,
        inviteNote, setInviteNote,
        generatedInvite,
        isCreatingInvite,
        redeemInviteToken, setRedeemInviteToken,
        isRedeemingInvite,
        registrationInviteTtlHours, setRegistrationInviteTtlHours,
        registrationInviteMaxUses, setRegistrationInviteMaxUses,
        registrationInviteBatchCount, setRegistrationInviteBatchCount,
        registrationInviteNote, setRegistrationInviteNote,
        registrationInvites,
        isCreatingRegistrationInvites,
        isRefreshingRegistrationInvites,
        isExportingRegistrationInvites,
        revokingRegistrationInviteCode,
        error, setError,
        createUsernameError,
        createPasswordError,
        runBasePrompt,
        effectiveRunPrompt,
        collectRunPromptOverrides,
        loadUsers,
        refreshLLMAccess,
        refreshFallbackChainSettings,
        ensureLLMReady,
        loadDocumentPromptSnapshot,
        onCreateUser,
        onSaveUserEdit,
        onSavePersonalKey,
        onClearPersonalKey,
        onSaveUserLLMDefault,
        onSaveGlobalLLMDefault,
        onSaveUserFallbackChain,
        onSaveGlobalFallbackChain,
        onSaveLearningPreferences,
        onCreateSharedInvite,
        onCreateRegistrationInvites,
        onRevokeRegistrationInvite,
        onExportRegistrationInvites,
        refreshRegistrationInvites,
        onRedeemInvite,
        onSavePrompt,
        onResetPrompt,
        onSaveDefaultPrompt,
        onUseSystemDefaultForMyPrompt,
    };
}
