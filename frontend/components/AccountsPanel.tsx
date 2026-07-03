import { useState } from "react";
import type { useSettingsAdmin } from "@/lib/hooks/useSettingsAdmin";
import type { AuthPolicy } from "@/lib/types";
import { formatIsoTime } from "@/lib/utils";

// Friendly descriptions for each permission
const PERM_LABELS = [
    { key: "can_manage_accounts", label: "管理用户", desc: "可创建账号、修改其他用户的权限" },
    { key: "can_manage_prompts", label: "管理 AI 指令", desc: "可修改系统默认的 AI 解释风格" },
    { key: "can_manage_shared_keys", label: "分发访问码", desc: "可生成邀请链接给新用户" },
] as const;

export function AccountsPanel({
    settings,
    authPolicy,
    canManageSharedKeys,
}: {
    settings: ReturnType<typeof useSettingsAdmin>;
    authPolicy: AuthPolicy;
    canManageSharedKeys: boolean;
}) {
    const [inviteExpanded, setInviteExpanded] = useState(false);
    const [createExpanded, setCreateExpanded] = useState(false);
    const [expandedUserId, setExpandedUserId] = useState<string | null>(null);
    const [userSearch, setUserSearch] = useState("");

    const {
        inviteTtlHours, setInviteTtlHours,
        inviteMaxUses, setInviteMaxUses,
        inviteNote, setInviteNote,
        generatedInvite, isCreatingInvite, onCreateSharedInvite,
        registrationInviteTtlHours, setRegistrationInviteTtlHours,
        registrationInviteMaxUses, setRegistrationInviteMaxUses,
        registrationInviteBatchCount, setRegistrationInviteBatchCount,
        registrationInviteNote, setRegistrationInviteNote,
        registrationInvites,
        isCreatingRegistrationInvites,
        isRefreshingRegistrationInvites,
        isExportingRegistrationInvites,
        revokingRegistrationInviteCode,
        onCreateRegistrationInvites,
        onRevokeRegistrationInvite,
        onExportRegistrationInvites,
        refreshRegistrationInvites,

        newUsername, setNewUsername,
        newPassword, setNewPassword,
        newRole, setNewRole,
        newIsActive, setNewIsActive,
        newCanUseSharedKey, setNewCanUseSharedKey,
        newPermissions, setNewPermissions,
        isCreatingUser, onCreateUser,
        createUsernameError, createPasswordError,

        userList, userEdits, patchUserDraft, savingUserId, onSaveUserEdit,
    } = settings;

    const isAdminRole = newRole === "admin";

    const filteredUsers = userSearch.trim()
        ? userList.filter((u) => u.username.toLowerCase().includes(userSearch.toLowerCase()))
        : userList;

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

            {/* ── 生成邀请码 ─────────────────────────────────────────────── */}
            {canManageSharedKeys && (
                <div className="card">
                    <div
                        className="card-header"
                        style={{ cursor: "pointer" }}
                        onClick={() => setInviteExpanded((v) => !v)}
                    >
                        <h3>生成邀请链接</h3>
                        <span style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
                            {inviteExpanded ? "▲ 收起" : "▼ 展开"}
                        </span>
                    </div>

                    {inviteExpanded && (
                        <>
                            <p style={{ margin: "0 0 14px", color: "var(--text-muted)", fontSize: "0.88rem" }}>
                                生成一个链接，发给用户后对方可直接获得 AI 使用权限，无需你手动配置账号。
                            </p>
                            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                                <div className="field-group">
                                    <div className="field-label">有效期</div>
                                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                        <input type="number" min={1} max={720} value={inviteTtlHours}
                                            onChange={(e) => setInviteTtlHours(Math.max(1, Math.min(720, Number(e.target.value) || 1)))}
                                            style={{ width: 80 }} />
                                        <span style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>小时</span>
                                    </div>
                                </div>
                                <div className="field-group">
                                    <div className="field-label">最多使用次数</div>
                                    <input type="number" min={1} max={1000} value={inviteMaxUses}
                                        onChange={(e) => setInviteMaxUses(Math.max(1, Math.min(1000, Number(e.target.value) || 1)))}
                                        style={{ width: 80 }} />
                                </div>
                                <div className="field-group" style={{ flex: 2 }}>
                                    <div className="field-label">备注（可选）</div>
                                    <input value={inviteNote} onChange={(e) => setInviteNote(e.target.value)}
                                        placeholder="例如：给小明的" style={{ width: "100%" }} />
                                </div>
                            </div>
                            <button className="primary" onClick={onCreateSharedInvite} disabled={isCreatingInvite} style={{ marginTop: 12 }}>
                                {isCreatingInvite ? "生成中…" : "生成邀请链接"}
                            </button>

                            {generatedInvite && (
                                <div style={{ marginTop: 14, padding: 12, background: "var(--surface-alt)", borderRadius: "var(--radius-sm)", border: "1px solid var(--line)" }}>
                                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: "0.82rem", color: "var(--text-muted)" }}>
                                        <span>过期：{formatIsoTime(generatedInvite.expires_at)}</span>
                                        <span>可用 {generatedInvite.max_uses} 次</span>
                                    </div>
                                    <textarea readOnly value={generatedInvite.invite_url}
                                        style={{ width: "100%", minHeight: 60, background: "#fff", fontSize: "0.82rem" }} />
                                    <button style={{ marginTop: 6, fontSize: "0.82rem" }}
                                        onClick={() => void navigator.clipboard.writeText(generatedInvite.invite_url)}>
                                        📋 复制链接
                                    </button>
                                </div>
                            )}
                        </>
                    )}
                </div>
            )}

            <div className="card">
                <div className="card-header">
                    <h3>注册邀请码（测试/半公益）</h3>
                    <div style={{ display: "flex", gap: 8 }}>
                        <button onClick={() => void refreshRegistrationInvites()} disabled={isRefreshingRegistrationInvites}>
                            {isRefreshingRegistrationInvites ? "刷新中…" : "刷新"}
                        </button>
                        <button onClick={onExportRegistrationInvites} disabled={isExportingRegistrationInvites}>
                            {isExportingRegistrationInvites ? "导出中…" : "导出 CSV"}
                        </button>
                    </div>
                </div>
                <p style={{ margin: "0 0 10px", color: "var(--text-muted)", fontSize: "0.88rem" }}>
                    默认建议：7 天有效、每个邀请码可用 20 次。注册链接会自动填码。
                </p>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                    <div className="field-group">
                        <div className="field-label">批量数量</div>
                        <input
                            type="number"
                            min={1}
                            max={200}
                            value={registrationInviteBatchCount}
                            onChange={(e) => setRegistrationInviteBatchCount(Math.max(1, Math.min(200, Number(e.target.value) || 1)))}
                            style={{ width: 90 }}
                        />
                    </div>
                    <div className="field-group">
                        <div className="field-label">有效期（小时）</div>
                        <input
                            type="number"
                            min={1}
                            max={720}
                            value={registrationInviteTtlHours}
                            onChange={(e) => setRegistrationInviteTtlHours(Math.max(1, Math.min(720, Number(e.target.value) || 1)))}
                            style={{ width: 90 }}
                        />
                    </div>
                    <div className="field-group">
                        <div className="field-label">单码可用次数</div>
                        <input
                            type="number"
                            min={1}
                            max={1000}
                            value={registrationInviteMaxUses}
                            onChange={(e) => setRegistrationInviteMaxUses(Math.max(1, Math.min(1000, Number(e.target.value) || 1)))}
                            style={{ width: 90 }}
                        />
                    </div>
                    <div className="field-group" style={{ flex: 1, minWidth: 200 }}>
                        <div className="field-label">备注</div>
                        <input
                            value={registrationInviteNote}
                            onChange={(e) => setRegistrationInviteNote(e.target.value)}
                            placeholder="例如：2月公开测试"
                            style={{ width: "100%" }}
                        />
                    </div>
                    <button className="primary" onClick={onCreateRegistrationInvites} disabled={isCreatingRegistrationInvites}>
                        {isCreatingRegistrationInvites ? "生成中…" : "批量生成"}
                    </button>
                </div>

                <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8, maxHeight: 320, overflow: "auto" }}>
                    {registrationInvites.length === 0 && <p className="meta">暂无注册邀请码。</p>}
                    {registrationInvites.map((item) => (
                        <div
                            key={`reg-invite-${item.code}`}
                            style={{
                                border: "1px solid var(--line)",
                                borderRadius: "var(--radius-sm)",
                                background: "var(--surface-alt)",
                                padding: "10px 12px",
                            }}
                        >
                            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                                    <strong>{item.code}</strong>
                                    {item.revoked && <span className="badge badge-red">已撤销</span>}
                                    {!item.revoked && item.remaining_uses <= 0 && <span className="badge badge-yellow">已用尽</span>}
                                    {!item.revoked && item.remaining_uses > 0 && <span className="badge badge-green">可用</span>}
                                </div>
                                <div style={{ display: "flex", gap: 8 }}>
                                    <button
                                        onClick={() => void navigator.clipboard.writeText(item.invite_url)}
                                        style={{ fontSize: "0.82rem" }}
                                    >
                                        复制链接
                                    </button>
                                    <button
                                        onClick={() => void onRevokeRegistrationInvite(item.code)}
                                        disabled={item.revoked || revokingRegistrationInviteCode === item.code}
                                        style={{ fontSize: "0.82rem" }}
                                    >
                                        {revokingRegistrationInviteCode === item.code ? "撤销中…" : "撤销"}
                                    </button>
                                </div>
                            </div>
                            <div className="meta" style={{ marginTop: 6 }}>
                                过期：{formatIsoTime(item.expires_at)} · 使用：{item.used_count}/{item.max_uses}
                                {item.note ? ` · 备注：${item.note}` : ""}
                            </div>
                            <textarea
                                readOnly
                                value={item.invite_url}
                                style={{ width: "100%", minHeight: 50, marginTop: 6, background: "#fff", fontSize: "0.8rem" }}
                            />
                        </div>
                    ))}
                </div>
            </div>

            {/* ── 创建账号 ────────────────────────────────────────────────── */}
            <div className="card">
                <div
                    className="card-header"
                    style={{ cursor: "pointer" }}
                    onClick={() => setCreateExpanded((v) => !v)}
                >
                    <h3>创建账号</h3>
                    <span style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
                        {createExpanded ? "▲ 收起" : "▼ 展开"}
                    </span>
                </div>

                {createExpanded && (
                    <div style={{ marginTop: 4 }}>
                        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                            <div className="field-group" style={{ flex: 1, minWidth: 180 }}>
                                <div className="field-label">用户名</div>
                                <input
                                    value={newUsername}
                                    onChange={(e) => setNewUsername(e.target.value)}
                                    placeholder={authPolicy.username.description}
                                    style={{ width: "100%" }}
                                />
                                {newUsername && createUsernameError && (
                                    <div style={{ color: "var(--bad)", fontSize: "0.78rem", marginTop: 4 }}>{createUsernameError}</div>
                                )}
                            </div>
                            <div className="field-group" style={{ flex: 1, minWidth: 180 }}>
                                <div className="field-label">密码</div>
                                <input
                                    type="password"
                                    value={newPassword}
                                    onChange={(e) => setNewPassword(e.target.value)}
                                    placeholder={authPolicy.password.description}
                                    style={{ width: "100%" }}
                                />
                                {newPassword && createPasswordError && (
                                    <div style={{ color: "var(--bad)", fontSize: "0.78rem", marginTop: 4 }}>{createPasswordError}</div>
                                )}
                            </div>
                        </div>

                        {/* Role selector */}
                        <div style={{ marginTop: 14 }}>
                            <div className="field-label">账号类型</div>
                            <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                                {(["user", "admin"] as const).map((r) => (
                                    <button key={r}
                                        className={newRole === r ? "primary" : ""}
                                        onClick={() => setNewRole(r)}
                                        style={{ fontSize: "0.85rem" }}>
                                        {r === "user" ? "普通用户" : "管理员（所有权限）"}
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Permissions — only shown for regular user */}
                        {!isAdminRole && (
                            <div style={{ marginTop: 14 }}>
                                <div className="field-label" style={{ marginBottom: 8 }}>附加权限</div>
                                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                    <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: "0.88rem" }}>
                                        <input type="checkbox" checked={newIsActive} onChange={(e) => setNewIsActive(e.target.checked)} />
                                        <span>账号启用<span style={{ color: "var(--text-muted)", marginLeft: 4 }}>（取消则账号无法登录）</span></span>
                                    </label>
                                    <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: "0.88rem" }}>
                                        <input type="checkbox" checked={newCanUseSharedKey} onChange={(e) => setNewCanUseSharedKey(e.target.checked)} />
                                        <span>允许使用 AI（平台共享访问）</span>
                                    </label>
                                    {PERM_LABELS.map(({ key, label, desc }) => (
                                        <label key={key} style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "pointer", fontSize: "0.88rem" }}>
                                            <input type="checkbox"
                                                checked={newPermissions[key]}
                                                onChange={(e) => setNewPermissions((prev) => ({ ...prev, [key]: e.target.checked }))}
                                                style={{ marginTop: 2 }} />
                                            <span>
                                                {label}
                                                <span style={{ color: "var(--text-muted)", marginLeft: 4, fontSize: "0.8rem" }}>— {desc}</span>
                                            </span>
                                        </label>
                                    ))}
                                </div>
                            </div>
                        )}

                        <button
                            className="primary"
                            style={{ marginTop: 16 }}
                            onClick={() => void onCreateUser()}
                            disabled={isCreatingUser || Boolean(createUsernameError) || Boolean(createPasswordError) || !newUsername || !newPassword}
                        >
                            {isCreatingUser ? "创建中…" : "创建账号"}
                        </button>
                    </div>
                )}
            </div>

            {/* ── 现有账号列表 ──────────────────────────────────────────── */}
            <div className="card">
                <div className="card-header" style={{ marginBottom: 12 }}>
                    <h3>现有账号（{userList.length} 个）</h3>
                </div>

                {/* 搜索框 */}
                {userList.length > 4 && (
                    <input
                        value={userSearch}
                        onChange={(e) => setUserSearch(e.target.value)}
                        placeholder="搜索用户名…"
                        style={{ width: "100%", marginBottom: 12 }}
                    />
                )}

                {filteredUsers.length === 0 && (
                    <p className="meta">{userSearch ? "无匹配用户。" : "暂无其他账号。"}</p>
                )}

                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {filteredUsers.map((u) => {
                        const draft = userEdits[u.id];
                        if (!draft) return null;
                        const isAdmin = draft.role === "admin";
                        const isExpanded = expandedUserId === u.id;

                        return (
                            <div key={u.id} style={{
                                border: "1px solid var(--line)", borderRadius: "var(--radius-sm)",
                                background: "var(--surface-alt)", overflow: "hidden"
                            }}>
                                {/* Collapsed header — always visible, click to toggle */}
                                <div
                                    style={{
                                        display: "flex", justifyContent: "space-between", alignItems: "center",
                                        gap: 8, padding: "10px 14px", cursor: "pointer", flexWrap: "wrap",
                                    }}
                                    onClick={() => setExpandedUserId(isExpanded ? null : u.id)}
                                >
                                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                        <strong style={{ fontSize: "0.92rem" }}>{u.username}</strong>
                                        <span className={`badge ${isAdmin ? "badge-blue" : "badge-gray"}`}>
                                            {isAdmin ? "管理员" : "普通用户"}
                                        </span>
                                        {!draft.is_active && <span className="badge badge-red">已停用</span>}
                                        {!isAdmin && draft.can_use_shared_key && <span className="badge badge-green">AI 已授权</span>}
                                    </div>
                                    <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                                        {isExpanded ? "▲ 收起" : "▼ 编辑"}
                                    </span>
                                </div>

                                {/* Expanded edit form */}
                                {isExpanded && (
                                    <div style={{ padding: "0 14px 14px", borderTop: "1px solid var(--line)" }}>
                                        {/* Role toggle */}
                                        <div style={{ marginTop: 12 }}>
                                            <div className="field-label" style={{ marginBottom: 6 }}>账号类型</div>
                                            <div style={{ display: "flex", gap: 8 }}>
                                                {(["user", "admin"] as const).map((r) => (
                                                    <button key={r}
                                                        className={draft.role === r ? "primary" : ""}
                                                        onClick={() => {
                                                            patchUserDraft(u.id, (prev) => {
                                                                if (r === "admin") return { ...prev, role: "admin", is_active: true, can_use_shared_key: true, permissions: { can_manage_accounts: true, can_manage_prompts: true, can_manage_shared_keys: true } };
                                                                return { ...prev, role: "user" };
                                                            });
                                                        }}
                                                        style={{ fontSize: "0.82rem" }}>
                                                        {r === "user" ? "普通用户" : "管理员（所有权限）"}
                                                    </button>
                                                ))}
                                            </div>
                                        </div>

                                        {!isAdmin && (
                                            <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                                                <div className="field-label" style={{ marginBottom: 4 }}>权限</div>
                                                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.85rem", cursor: "pointer" }}>
                                                    <input type="checkbox" checked={draft.is_active}
                                                        onChange={(e) => patchUserDraft(u.id, (prev) => ({ ...prev, is_active: e.target.checked }))} />
                                                    账号启用
                                                    <span style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>（取消后无法登录）</span>
                                                </label>
                                                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.85rem", cursor: "pointer" }}>
                                                    <input type="checkbox" checked={draft.can_use_shared_key}
                                                        onChange={(e) => patchUserDraft(u.id, (prev) => ({ ...prev, can_use_shared_key: e.target.checked }))} />
                                                    允许使用 AI（平台共享访问）
                                                </label>
                                                {PERM_LABELS.map(({ key, label, desc }) => (
                                                    <label key={key} style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: "0.85rem", cursor: "pointer" }}>
                                                        <input type="checkbox"
                                                            checked={draft.permissions[key]}
                                                            onChange={(e) => patchUserDraft(u.id, (prev) => ({ ...prev, permissions: { ...prev.permissions, [key]: e.target.checked } }))}
                                                            style={{ marginTop: 2 }} />
                                                        <span>{label}<span style={{ color: "var(--text-muted)", marginLeft: 4, fontSize: "0.78rem" }}>— {desc}</span></span>
                                                    </label>
                                                ))}
                                            </div>
                                        )}

                                        <button
                                            className="primary"
                                            style={{ marginTop: 14, fontSize: "0.85rem" }}
                                            onClick={() => { void onSaveUserEdit(u.id); setExpandedUserId(null); }}
                                            disabled={savingUserId === u.id}
                                        >
                                            {savingUserId === u.id ? "保存中…" : "保存"}
                                        </button>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}
