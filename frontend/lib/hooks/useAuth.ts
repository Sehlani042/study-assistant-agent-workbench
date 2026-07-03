import { useState, useEffect, useMemo } from "react";
import {
    getAuthPolicy,
    getAuthToken,
    getCurrentUser,
    clearAuthToken,
    login,
    logout,
    registerUser,
    sendRegisterEmailCode,
} from "@/lib/api";
import type { AuthPolicy, UserPayload } from "@/lib/types";
import {
    FALLBACK_AUTH_POLICY,
    validateUsernameByPolicy,
    validatePasswordByPolicy,
    normalizeUsernameInput,
    extractRetryAfterSeconds,
    SESSION_KEY,
} from "@/lib/utils";

export function useAuth() {
    const [authPolicy, setAuthPolicy] = useState<AuthPolicy>(FALLBACK_AUTH_POLICY);
    const [authReady, setAuthReady] = useState(false);
    const [currentUser, setCurrentUser] = useState<UserPayload | null>(null);
    const [loginUsername, setLoginUsername] = useState("");
    const [loginPassword, setLoginPassword] = useState("");
    const [registerUsername, setRegisterUsername] = useState("");
    const [registerPassword, setRegisterPassword] = useState("");
    const [registerEmail, setRegisterEmail] = useState("");
    const [registerEmailCode, setRegisterEmailCode] = useState("");
    const [registerInviteCode, setRegisterInviteCode] = useState("");
    const [isLoggingIn, setIsLoggingIn] = useState(false);
    const [isRegistering, setIsRegistering] = useState(false);
    const [isSendingRegisterCode, setIsSendingRegisterCode] = useState(false);
    const [registerCodeCooldownUntil, setRegisterCodeCooldownUntil] = useState(0);
    const [registerCodeClockMs, setRegisterCodeClockMs] = useState(() => Date.now());
    const [error, setError] = useState<string | null>(null);

    const loginUsernameError = useMemo(() => validateUsernameByPolicy(authPolicy, loginUsername), [authPolicy, loginUsername]);
    const loginPasswordError = useMemo(() => validatePasswordByPolicy(authPolicy, loginPassword), [authPolicy, loginPassword]);
    const registerUsernameError = useMemo(
        () => validateUsernameByPolicy(authPolicy, registerUsername),
        [authPolicy, registerUsername],
    );
    const registerPasswordError = useMemo(
        () => validatePasswordByPolicy(authPolicy, registerPassword),
        [authPolicy, registerPassword],
    );
    const registerCodeResendSeconds = useMemo(() => {
        const value = Number(authPolicy.registration?.email_code_resend_seconds ?? 60);
        if (!Number.isFinite(value) || value <= 0) return 60;
        return Math.floor(value);
    }, [authPolicy.registration?.email_code_resend_seconds]);
    const registerCodeCooldownRemaining = useMemo(() => {
        if (!registerCodeCooldownUntil) return 0;
        const remainingMs = registerCodeCooldownUntil - registerCodeClockMs;
        if (remainingMs <= 0) return 0;
        return Math.ceil(remainingMs / 1000);
    }, [registerCodeCooldownUntil, registerCodeClockMs]);

    useEffect(() => {
        let cancelled = false;

        const boot = async () => {
            try {
                const policy = await getAuthPolicy();
                if (!cancelled) setAuthPolicy(policy);
            } catch {
                // Fallback to built-in
            }
            const token = getAuthToken();
            if (!token) {
                if (!cancelled) setAuthReady(true);
                return;
            }
            try {
                const me = await getCurrentUser();
                if (!cancelled) setCurrentUser(me);
            } catch {
                clearAuthToken();
                if (!cancelled) setCurrentUser(null);
            } finally {
                if (!cancelled) setAuthReady(true);
            }
        };

        boot();
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        try {
            const params = new URLSearchParams(window.location.search);
            const invite = params.get("registerInvite");
            if (invite && invite.trim()) {
                setRegisterInviteCode(invite.trim().toUpperCase());
            }
        } catch {
            // ignore invalid URL state
        }
    }, []);

    useEffect(() => {
        if (!registerCodeCooldownUntil) return;
        if (registerCodeCooldownUntil <= Date.now()) {
            setRegisterCodeClockMs(Date.now());
            return;
        }
        const timer = window.setInterval(() => {
            const now = Date.now();
            setRegisterCodeClockMs(now);
            if (now >= registerCodeCooldownUntil) {
                window.clearInterval(timer);
            }
        }, 500);
        return () => window.clearInterval(timer);
    }, [registerCodeCooldownUntil]);

    const onLogin = async () => {
        const usernameErr = validateUsernameByPolicy(authPolicy, loginUsername);
        const passwordErr = validatePasswordByPolicy(authPolicy, loginPassword);
        if (usernameErr || passwordErr) {
            setError(usernameErr ?? passwordErr);
            return;
        }
        setError(null);
        setIsLoggingIn(true);
        try {
            const payload = await login(normalizeUsernameInput(loginUsername), loginPassword);
            setCurrentUser(payload.user);
        } catch (err) {
            setError(err instanceof Error ? err.message : "登录失败");
        } finally {
            setIsLoggingIn(false);
        }
    };

    const onRegister = async () => {
        if (authPolicy.registration?.enabled === false) {
            setError("当前服务已关闭注册，请联系管理员。");
            return;
        }
        const usernameErr = validateUsernameByPolicy(authPolicy, registerUsername);
        const passwordErr = validatePasswordByPolicy(authPolicy, registerPassword);
        if (usernameErr || passwordErr) {
            setError(usernameErr ?? passwordErr);
            return;
        }
        if (authPolicy.registration?.invite_required && !registerInviteCode.trim()) {
            setError("注册需要邀请码");
            return;
        }
        if (authPolicy.registration?.email_verification_required) {
            const email = registerEmail.trim();
            if (!email) {
                setError("请先填写邮箱");
                return;
            }
            if (!registerEmailCode.trim()) {
                setError("请填写邮箱验证码");
                return;
            }
        }

        setError(null);
        setIsRegistering(true);
        try {
            await registerUser({
                username: normalizeUsernameInput(registerUsername),
                password: registerPassword,
                email: registerEmail.trim() || undefined,
                email_code: registerEmailCode.trim() || undefined,
                invite_code: registerInviteCode.trim() || undefined,
            });
            const payload = await login(normalizeUsernameInput(registerUsername), registerPassword);
            setCurrentUser(payload.user);
            setRegisterUsername("");
            setRegisterPassword("");
            setRegisterEmail("");
            setRegisterEmailCode("");
            setRegisterInviteCode("");
        } catch (err) {
            setError(err instanceof Error ? err.message : "注册失败");
        } finally {
            setIsRegistering(false);
        }
    };

    const onSendRegisterEmailCode = async () => {
        if (!registerEmail.trim()) {
            setError("请先填写邮箱");
            return;
        }
        if (registerCodeCooldownRemaining > 0) {
            setError(`请等待 ${registerCodeCooldownRemaining} 秒后再发送验证码`);
            return;
        }
        setError(null);
        setIsSendingRegisterCode(true);
        try {
            const payload = await sendRegisterEmailCode(registerEmail.trim());
            const waitSeconds = Math.max(
                1,
                Number(payload.resend_after_seconds || registerCodeResendSeconds) || registerCodeResendSeconds,
            );
            setRegisterCodeCooldownUntil(Date.now() + waitSeconds * 1000);
            setError(`验证码已发送到 ${payload.masked_email}（有效期 ${payload.ttl_minutes} 分钟，${waitSeconds} 秒后可重发）`);
        } catch (err) {
            const message = err instanceof Error ? err.message : "发送验证码失败";
            const retryAfter = extractRetryAfterSeconds(message);
            if (retryAfter && retryAfter > 0) {
                setRegisterCodeCooldownUntil(Date.now() + retryAfter * 1000);
            }
            setError(message);
        } finally {
            setIsSendingRegisterCode(false);
        }
    };

    const clearAuthFields = () => {
        setRegisterUsername("");
        setRegisterPassword("");
        setRegisterEmail("");
        setRegisterEmailCode("");
        setRegisterInviteCode("");
        setRegisterCodeCooldownUntil(0);
        setRegisterCodeClockMs(Date.now());
    };

    const onLogout = async () => {
        setError(null);
        try {
            await logout();
        } catch {
            clearAuthToken();
        }
        setCurrentUser(null);
        clearAuthFields();
        window.localStorage.removeItem(SESSION_KEY);
    };

    return {
        authPolicy,
        authReady,
        currentUser,
        setCurrentUser,
        loginUsername,
        setLoginUsername,
        loginPassword,
        setLoginPassword,
        registerUsername,
        setRegisterUsername,
        registerPassword,
        setRegisterPassword,
        registerEmail,
        setRegisterEmail,
        registerEmailCode,
        setRegisterEmailCode,
        registerInviteCode,
        setRegisterInviteCode,
        isLoggingIn,
        isRegistering,
        isSendingRegisterCode,
        registerCodeCooldownRemaining,
        registerCodeResendSeconds,
        error,
        setError,
        loginUsernameError,
        loginPasswordError,
        registerUsernameError,
        registerPasswordError,
        onLogin,
        onRegister,
        onSendRegisterEmailCode,
        onLogout,
        clearAuthFields,
    };
}
