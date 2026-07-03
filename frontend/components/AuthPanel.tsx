"use client";

import { useEffect, useState } from "react";
import type { useAuth } from "@/lib/hooks/useAuth";

export function AuthPanel({ auth }: { auth: ReturnType<typeof useAuth> }) {
    const [tab, setTab] = useState<"login" | "register">("login");

    const {
        loginUsername, setLoginUsername,
        loginPassword, setLoginPassword,
        isLoggingIn, onLogin,
        registerUsername, setRegisterUsername,
        registerPassword, setRegisterPassword,
        registerEmail, setRegisterEmail,
        registerEmailCode, setRegisterEmailCode,
        registerInviteCode, setRegisterInviteCode,
        isRegistering, onRegister,
        authPolicy, error, setError,
    } = auth;

    const regEnabled = authPolicy.registration?.enabled ?? false;

    useEffect(() => {
        if (!regEnabled) return;
        if (authPolicy.registration?.invite_required && registerInviteCode.trim()) {
            setTab("register");
        }
    }, [authPolicy.registration?.invite_required, regEnabled, registerInviteCode]);

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        await onLogin();
    };

    const handleRegister = async (e: React.FormEvent) => {
        e.preventDefault();
        await onRegister();
    };

    return (
        <div className="auth-shell">
            <div className="auth-card">
                <div className="auth-brand">
                    <h1>学习助手</h1>
                    <p>上传文档，AI 生成逐页解释与问答</p>
                </div>

                {regEnabled && (
                    <div className="auth-tabs">
                        <button
                            className={`auth-tab ${tab === "login" ? "active" : ""}`}
                            onClick={() => { setTab("login"); setError(null); }}
                        >
                            登录
                        </button>
                        <button
                            className={`auth-tab ${tab === "register" ? "active" : ""}`}
                            onClick={() => { setTab("register"); setError(null); }}
                        >
                            注册
                        </button>
                    </div>
                )}

                {error && <div className="auth-error">{error}</div>}

                {tab === "login" && (
                    <form onSubmit={handleLogin}>
                        <div className="auth-field">
                            <label>用户名</label>
                            <input
                                type="text"
                                value={loginUsername}
                                onChange={(e) => setLoginUsername(e.target.value)}
                                placeholder="输入用户名"
                                autoComplete="username"
                                autoFocus
                            />
                        </div>
                        <div className="auth-field">
                            <label>密码</label>
                            <input
                                type="password"
                                value={loginPassword}
                                onChange={(e) => setLoginPassword(e.target.value)}
                                placeholder="输入密码"
                                autoComplete="current-password"
                            />
                        </div>
                        <button type="submit" className="auth-submit" disabled={isLoggingIn}>
                            {isLoggingIn ? "登录中…" : "登录"}
                        </button>
                    </form>
                )}

                {tab === "register" && regEnabled && (
                    <form onSubmit={handleRegister}>
                        <div className="auth-field">
                            <label>用户名</label>
                            <input
                                type="text"
                                value={registerUsername}
                                onChange={(e) => setRegisterUsername(e.target.value)}
                                placeholder={authPolicy.username.description}
                                autoComplete="username"
                            />
                            <div className="field-hint">{authPolicy.username.description}</div>
                        </div>
                        <div className="auth-field">
                            <label>密码</label>
                            <input
                                type="password"
                                value={registerPassword}
                                onChange={(e) => setRegisterPassword(e.target.value)}
                                placeholder={authPolicy.password.description}
                                autoComplete="new-password"
                            />
                            <div className="field-hint">{authPolicy.password.description}</div>
                        </div>
                        <div className="auth-field">
                            <label>邮箱（可选）</label>
                            <input
                                type="email"
                                value={registerEmail}
                                onChange={(e) => setRegisterEmail(e.target.value)}
                                placeholder="email@example.com"
                                autoComplete="email"
                            />
                        </div>
                        {authPolicy.registration?.invite_required && (
                            <div className="auth-field">
                                <label>邀请码</label>
                                <input
                                    type="text"
                                    value={registerInviteCode}
                                    onChange={(e) => setRegisterInviteCode(e.target.value)}
                                    placeholder="注册需要邀请码"
                                />
                            </div>
                        )}
                        <button type="submit" className="auth-submit" disabled={isRegistering}>
                            {isRegistering ? "注册中…" : "注册账号"}
                        </button>
                    </form>
                )}
            </div>
        </div>
    );
}
