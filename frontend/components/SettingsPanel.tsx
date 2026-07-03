import { useMemo, useState } from "react";

import type { useSettingsAdmin } from "@/lib/hooks/useSettingsAdmin";
import type { LearningPreferences } from "@/lib/types";

function resolutionLabel(source: string): string {
    if (source === "discovered-3.1") return "已解析到 3.1";
    if (source === "fallback-2.5-lite") return "已自动回退";
    return "精确模型";
}

export function SettingsPanel({
    settings,
    canManageSharedKeys,
    canManageAccounts,
}: {
    settings: ReturnType<typeof useSettingsAdmin>;
    canManageSharedKeys: boolean;
    canManageAccounts: boolean;
}) {
    const [showAdvancedKeys, setShowAdvancedKeys] = useState(false);
    const [showAdvancedModels, setShowAdvancedModels] = useState(false);

    const {
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
        personalApiKey,
        setPersonalApiKey,
        userProviderDraft,
        setUserProviderDraft,
        userModelDraft,
        setUserModelDraft,
        globalProviderDraft,
        setGlobalProviderDraft,
        globalModelDraft,
        setGlobalModelDraft,
        isSavingLLMUserDefault,
        isSavingLLMGlobalDefault,
        isSavingPersonalKey,
        isClearingPersonalKey,
        fallbackChainSettings,
        learningPreferences,
        userFallbackChainDraft,
        setUserFallbackChainDraft,
        globalFallbackChainDraft,
        setGlobalFallbackChainDraft,
        isSavingUserFallbackChain,
        isSavingGlobalFallbackChain,
        isSavingLearningPreferences,
        redeemInviteToken,
        setRedeemInviteToken,
        isRedeemingInvite,
        onSaveUserLLMDefault,
        onSaveGlobalLLMDefault,
        onSaveUserFallbackChain,
        onSaveGlobalFallbackChain,
        onSaveLearningPreferences,
        onSavePersonalKey,
        onClearPersonalKey,
        onRedeemInvite,
    } = settings;

    const selectedAccess = providerAccess || llmAccess;
    const isReady = Boolean(selectedAccess && (selectedAccess.can_use_shared_key || selectedAccess.has_personal_key));
    const userProviderOption = providerOptionMap[userProviderDraft];
    const globalProviderOption = providerOptionMap[globalProviderDraft];
    const userRecommendedModels = userProviderOption?.recommended_models || [];
    const globalRecommendedModels = globalProviderOption?.recommended_models || [];
    const fallbackTemplates = useMemo(
        () => [
            {
                label: "学习优先",
                chain: "gemini:flash, gemini:pro, openai:gpt-5.2-mini",
            },
            {
                label: "纯 Gemini",
                chain: "gemini:flash, gemini:pro",
            },
        ],
        [],
    );
    const showResolutionHint = Boolean(
        effectiveResolutionSource && effectiveResolutionSource !== "exact" && effectiveResolvedModel,
    );

    return (
        <div className="settings-layout">
            <div className="card settings-hero-card">
                <div className="card-header">
                    <div>
                        <h3>当前可用模型</h3>
                        <p className="card-subtitle">优先展示学习所需的实际可用模型，而不是把配置细节压给用户。</p>
                    </div>
                    <span className={`badge ${isReady ? "badge-green" : "badge-yellow"}`}>
                        {isReady ? "可直接使用" : "需要配置"}
                    </span>
                </div>

                <div className="settings-current-model">
                    <div className="settings-current-main">
                        <div className="settings-model-chip">{effectiveProvider}</div>
                        <div>
                            <div className="settings-current-title">{effectiveDisplayLabel || effectiveModel || effectiveProvider}</div>
                            {effectiveResolvedModel && (
                                <div className="meta">
                                    实际 API ID：<code>{effectiveResolvedModel}</code>
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="settings-current-meta">
                        {showResolutionHint && (
                            <span className="badge badge-blue">{resolutionLabel(effectiveResolutionSource)}</span>
                        )}
                        {!selectedAccess?.requires_personal_key && (
                            <span className="badge badge-green">平台已提供访问权限</span>
                        )}
                        {selectedAccess?.requires_personal_key && selectedAccess?.has_personal_key && (
                            <span className="badge badge-blue">使用个人 Key</span>
                        )}
                        {selectedAccess?.requires_personal_key && !selectedAccess?.has_personal_key && (
                            <span className="badge badge-yellow">待填写个人 Key</span>
                        )}
                    </div>
                </div>
            </div>

            <div className="card">
                <div className="card-header">
                    <div>
                        <h3>学习参数默认值</h3>
                        <p className="card-subtitle">先用结构化参数控制讲解风格，普通用户不必直接改 Prompt。</p>
                    </div>
                    <button className="primary" onClick={onSaveLearningPreferences} disabled={isSavingLearningPreferences}>
                        {isSavingLearningPreferences ? "保存中…" : "保存学习参数"}
                    </button>
                </div>

                <div className="settings-row" style={{ flexWrap: "wrap" }}>
                    <select
                        value={learningPreferences.learner_level}
                        onChange={(e) => settings.setLearningPreferences((prev: typeof learningPreferences) => ({ ...prev, learner_level: e.target.value as LearningPreferences["learner_level"] }))}
                    >
                        <option value="beginner">入门</option>
                        <option value="intermediate">进阶</option>
                        <option value="advanced">高级</option>
                    </select>
                    <select
                        value={learningPreferences.learning_goal}
                        onChange={(e) => settings.setLearningPreferences((prev: typeof learningPreferences) => ({ ...prev, learning_goal: e.target.value as LearningPreferences["learning_goal"] }))}
                    >
                        <option value="understand">先讲懂</option>
                        <option value="learn_and_apply">学会应用</option>
                        <option value="exam">考试导向</option>
                    </select>
                    <select
                        value={learningPreferences.depth_mode}
                        onChange={(e) => settings.setLearningPreferences((prev: typeof learningPreferences) => ({ ...prev, depth_mode: e.target.value as LearningPreferences["depth_mode"] }))}
                    >
                        <option value="quick">Quick</option>
                        <option value="standard">Standard</option>
                        <option value="deep">Deep</option>
                    </select>
                    <select
                        value={learningPreferences.attention_support}
                        onChange={(e) => settings.setLearningPreferences((prev: typeof learningPreferences) => ({ ...prev, attention_support: e.target.value as LearningPreferences["attention_support"] }))}
                    >
                        <option value="standard">标准</option>
                        <option value="adhd_friendly">ADHD 友好</option>
                    </select>
                </div>
            </div>

            <div className="card">
                <div className="card-header">
                    <div>
                        <h3>默认模型与兜底链</h3>
                        <p className="card-subtitle">先选你平时默认用什么，再决定失败时怎么兜底。</p>
                    </div>
                    <button onClick={() => setShowAdvancedModels((prev) => !prev)}>
                        {showAdvancedModels ? "收起高级模式" : "展开高级模式"}
                    </button>
                </div>

                <div className="settings-block">
                    <div className="settings-block-title">个人默认</div>
                    <div className="settings-row">
                        <select
                            value={userProviderDraft}
                            onChange={(e) => {
                                const next = e.target.value;
                                setUserProviderDraft(next);
                                const nextModel = llmSettings?.global_default?.default_models?.[next]
                                    || providerOptionMap[next]?.recommended_models?.[0]?.id
                                    || "";
                                if (!userModelDraft.trim() || providerOptionMap[next]?.recommended_models?.some((item) => item.id === userModelDraft)) {
                                    setUserModelDraft(nextModel);
                                }
                            }}
                        >
                            {providerOptions.map((provider) => (
                                <option key={`user-provider-${provider.id}`} value={provider.id}>
                                    {provider.label}
                                </option>
                            ))}
                        </select>
                        <button className="primary" onClick={onSaveUserLLMDefault} disabled={isSavingLLMUserDefault}>
                            {isSavingLLMUserDefault ? "保存中…" : "保存个人默认"}
                        </button>
                    </div>

                    <div className="settings-recommended-list">
                        {userRecommendedModels.map((item) => (
                            <button
                                key={`user-model-${item.id}`}
                                className={`settings-model-pill ${userModelDraft === item.id ? "active" : ""}`}
                                onClick={() => setUserModelDraft(item.id)}
                            >
                                <span>{item.display_label}</span>
                                {item.resolution_source !== "exact" && <small>{resolutionLabel(item.resolution_source)}</small>}
                            </button>
                        ))}
                    </div>

                    <div className="settings-inline-note">
                        当前填写值：<code>{userModelDraft || "未设置"}</code>
                    </div>

                    {showAdvancedModels && (
                        <input
                            value={userModelDraft}
                            onChange={(e) => setUserModelDraft(e.target.value)}
                            placeholder={userProviderDraft === "openai" ? "如 gpt-5.2" : userProviderDraft === "gemini" ? "如 gemini-3.1-flash-lite" : "模型名"}
                            style={{ width: "100%", marginTop: 10 }}
                        />
                    )}
                </div>

                {canManageAccounts && (
                    <details className="settings-admin-box">
                        <summary>管理员：全局默认模型</summary>
                        <div className="settings-row" style={{ marginTop: 12 }}>
                            <select
                                value={globalProviderDraft}
                                onChange={(e) => {
                                    const next = e.target.value;
                                    setGlobalProviderDraft(next);
                                    const nextModel = llmSettings?.global_default?.default_models?.[next]
                                        || providerOptionMap[next]?.recommended_models?.[0]?.id
                                        || "";
                                    setGlobalModelDraft(nextModel);
                                }}
                            >
                                {providerOptions.map((provider) => (
                                    <option key={`global-provider-${provider.id}`} value={provider.id}>
                                        {provider.label}
                                    </option>
                                ))}
                            </select>
                            <button className="primary" onClick={onSaveGlobalLLMDefault} disabled={isSavingLLMGlobalDefault}>
                                {isSavingLLMGlobalDefault ? "保存中…" : "保存全局默认"}
                            </button>
                        </div>
                        <div className="settings-recommended-list">
                            {globalRecommendedModels.map((item) => (
                                <button
                                    key={`global-model-${item.id}`}
                                    className={`settings-model-pill ${globalModelDraft === item.id ? "active" : ""}`}
                                    onClick={() => setGlobalModelDraft(item.id)}
                                >
                                    <span>{item.display_label}</span>
                                    {item.resolution_source !== "exact" && <small>{resolutionLabel(item.resolution_source)}</small>}
                                </button>
                            ))}
                        </div>
                        {showAdvancedModels && (
                            <input
                                value={globalModelDraft}
                                onChange={(e) => setGlobalModelDraft(e.target.value)}
                                placeholder={globalProviderDraft === "openai" ? "如 gpt-5.2" : globalProviderDraft === "gemini" ? "如 gemini-3.1-flash-lite" : "模型名"}
                                style={{ width: "100%", marginTop: 10 }}
                            />
                        )}
                    </details>
                )}

                <div className="settings-divider" />

                <div className="settings-block">
                    <div className="settings-block-title">兜底链策略</div>
                    <div className="settings-template-row">
                        {fallbackTemplates.map((item) => (
                            <button key={item.label} onClick={() => setUserFallbackChainDraft(item.chain)}>
                                {item.label}
                            </button>
                        ))}
                    </div>
                    <div className="settings-row">
                        <input
                            value={userFallbackChainDraft}
                            onChange={(e) => setUserFallbackChainDraft(e.target.value)}
                            placeholder="个人兜底链"
                            style={{ flex: 1, minWidth: 260 }}
                        />
                        <button className="primary" onClick={onSaveUserFallbackChain} disabled={isSavingUserFallbackChain}>
                            {isSavingUserFallbackChain ? "保存中…" : "保存个人链"}
                        </button>
                    </div>
                    {canManageAccounts && (
                        <div className="settings-row" style={{ marginTop: 10 }}>
                            <input
                                value={globalFallbackChainDraft}
                                onChange={(e) => setGlobalFallbackChainDraft(e.target.value)}
                                placeholder="系统兜底链"
                                style={{ flex: 1, minWidth: 260 }}
                            />
                            <button className="primary" onClick={onSaveGlobalFallbackChain} disabled={isSavingGlobalFallbackChain}>
                                {isSavingGlobalFallbackChain ? "保存中…" : "保存系统链"}
                            </button>
                        </div>
                    )}
                    {fallbackChainSettings && (
                        <div className="settings-inline-note">
                            当前生效来源：<strong>{fallbackChainSettings.source}</strong>
                            {" · "}
                            {fallbackChainSettings.effective.join(" → ") || "未设置"}
                        </div>
                    )}
                </div>
            </div>

            <div className="card">
                <div className="card-header">
                    <div>
                        <h3>个人 API Key / 邀请码</h3>
                        <p className="card-subtitle">有平台权限时默认不打扰；需要时再展开高级配置。</p>
                    </div>
                    {!selectedAccess?.requires_personal_key && isReady && (
                        <button onClick={() => setShowAdvancedKeys((prev) => !prev)}>
                            {showAdvancedKeys ? "收起高级 Key 设置" : "展开高级 Key 设置"}
                        </button>
                    )}
                </div>

                {!selectedAccess?.requires_personal_key && isReady && !showAdvancedKeys && (
                    <div className="settings-inline-note">
                        平台已经为你准备好了可用模型。你可以直接回到学习工作台上传文档，不需要额外配置 Key。
                    </div>
                )}

                {(selectedAccess?.requires_personal_key || showAdvancedKeys) && (
                    <>
                        <p style={{ margin: "0 0 14px", color: "var(--text-muted)", fontSize: "0.9rem" }}>
                            {selectedAccess?.has_personal_key
                                ? `你已经配置了个人 ${effectiveProvider} Key。若要替换，在下方输入新 Key 并保存。`
                                : `当前账号需要一个 ${effectiveProvider} Key 才能开始生成。`}
                        </p>
                        <div className="settings-row">
                            <input
                                type="password"
                                value={personalApiKey}
                                onChange={(e) => setPersonalApiKey(e.target.value)}
                                placeholder={`粘贴你的 ${effectiveProvider} API Key`}
                                style={{ flex: 1, minWidth: 260 }}
                            />
                            <button className="primary" onClick={onSavePersonalKey} disabled={isSavingPersonalKey || !personalApiKey.trim()}>
                                {isSavingPersonalKey ? "保存中…" : "保存"}
                            </button>
                            {selectedAccess?.has_personal_key && (
                                <button onClick={onClearPersonalKey} disabled={isClearingPersonalKey}>
                                    {isClearingPersonalKey ? "清除中…" : "清除 Key"}
                                </button>
                            )}
                        </div>

                        {effectiveProvider === "gemini" && (
                            <p style={{ margin: "8px 0 0", fontSize: "0.78rem", color: "var(--text-muted)" }}>
                                在 <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" style={{ color: "var(--primary)" }}>Google AI Studio</a> 申请 Gemini Key
                            </p>
                        )}
                        {effectiveProvider === "openai" && (
                            <p style={{ margin: "8px 0 0", fontSize: "0.78rem", color: "var(--text-muted)" }}>
                                在 <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer" style={{ color: "var(--primary)" }}>OpenAI Platform</a> 创建 OpenAI Key
                            </p>
                        )}
                    </>
                )}

                {!canManageSharedKeys && (
                    <div className="settings-divider" />
                )}

                {!canManageSharedKeys && (
                    <div className="settings-block">
                        <div className="settings-block-title">兑换平台访问码</div>
                        <div className="settings-row">
                            <input
                                value={redeemInviteToken}
                                onChange={(e) => setRedeemInviteToken(e.target.value)}
                                placeholder="粘贴邀请 token…"
                                style={{ flex: 1, minWidth: 260 }}
                            />
                            <button className="primary" onClick={onRedeemInvite} disabled={isRedeemingInvite || !redeemInviteToken.trim()}>
                                {isRedeemingInvite ? "兑换中…" : "兑换"}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
