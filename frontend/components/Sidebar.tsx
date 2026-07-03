import { useEffect, useMemo, useState } from "react";

import type { DashboardPage, UserPayload } from "@/lib/types";

type NavItem = {
    id: DashboardPage;
    label: string;
    icon: string;
};

const PRIMARY_ITEMS: NavItem[] = [
    { id: "study", label: "学习工作台", icon: "📖" },
    { id: "history", label: "历史文档", icon: "🗂️" },
];

const MANAGEMENT_ITEMS: NavItem[] = [
    { id: "settings", label: "模型设置", icon: "⚙️" },
    { id: "queue", label: "任务队列", icon: "🧵" },
    { id: "prompts", label: "Prompt 配置", icon: "🧠" },
    { id: "lab", label: "Explanation Lab", icon: "🧪" },
    { id: "accounts", label: "账户管理", icon: "👥" },
];

export function Sidebar({
    activePage,
    onNavigate,
    currentUser,
    onLogout,
    llmStatusText,
    collapsed,
    onToggleCollapsed,
    mobileOpen,
    onCloseMobile,
}: {
    activePage: DashboardPage;
    onNavigate: (page: DashboardPage) => void;
    currentUser: UserPayload;
    onLogout: () => void;
    llmStatusText?: string;
    collapsed: boolean;
    onToggleCollapsed: () => void;
    mobileOpen: boolean;
    onCloseMobile: () => void;
}) {
    const isAdmin = currentUser.role === "admin";
    const managementItems = useMemo(
        () =>
            MANAGEMENT_ITEMS.filter((item) => {
                if (item.id === "accounts") return isAdmin;
                if (item.id === "lab") {
                    return Boolean(isAdmin || currentUser.permissions?.can_manage_prompts);
                }
                return true;
            }),
        [currentUser.permissions?.can_manage_prompts, isAdmin],
    );
    const managementActive = managementItems.some((item) => item.id === activePage);
    const [managementOpen, setManagementOpen] = useState(managementActive);

    useEffect(() => {
        if (managementActive) {
            setManagementOpen(true);
        }
    }, [managementActive]);

    return (
        <>
            <button
                className={`sidebar-backdrop ${mobileOpen ? "show" : ""}`}
                aria-label="关闭侧栏"
                onClick={onCloseMobile}
            />

            <nav className={`sidebar ${collapsed ? "collapsed" : ""} ${mobileOpen ? "mobile-open" : ""}`}>
                <div className="sidebar-brand">
                    <div className="sidebar-brand-row">
                        <div className="sidebar-brand-text">
                            <h1>Study Assistant</h1>
                            <div className="tagline">学习优先 · 三层 Agent · LaTeX</div>
                        </div>
                        <button
                            className="sidebar-collapse-btn"
                            onClick={onToggleCollapsed}
                            title={collapsed ? "展开侧栏" : "收起侧栏"}
                            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
                        >
                            {collapsed ? "»" : "«"}
                        </button>
                        <button className="sidebar-mobile-close" onClick={onCloseMobile} aria-label="关闭菜单">
                            ✕
                        </button>
                    </div>
                </div>

                <div className="sidebar-nav">
                    <div className="sidebar-group-label">学习区</div>
                    {PRIMARY_ITEMS.map((item) => (
                        <button
                            key={item.id}
                            className={`sidebar-nav-item ${activePage === item.id ? "active" : ""}`}
                            onClick={() => {
                                onNavigate(item.id);
                                onCloseMobile();
                            }}
                            title={item.label}
                        >
                            <span className="nav-icon">{item.icon}</span>
                            <span className="nav-label">{item.label}</span>
                        </button>
                    ))}

                    <div className="sidebar-group">
                        <button
                            className={`sidebar-nav-item sidebar-group-toggle ${managementActive ? "active" : ""}`}
                            onClick={() => {
                                if (collapsed) {
                                    onToggleCollapsed();
                                    return;
                                }
                                setManagementOpen((prev) => !prev);
                            }}
                            title="设置与管理"
                        >
                            <span className="nav-icon">🧰</span>
                            <span className="nav-label">设置与管理</span>
                            <span className="sidebar-group-arrow">{managementOpen ? "▾" : "▸"}</span>
                        </button>
                        {!collapsed && managementOpen && (
                            <div className="sidebar-subnav">
                                {managementItems.map((item) => (
                                    <button
                                        key={item.id}
                                        className={`sidebar-subnav-item ${activePage === item.id ? "active" : ""}`}
                                        onClick={() => {
                                            onNavigate(item.id);
                                            onCloseMobile();
                                        }}
                                    >
                                        <span className="nav-icon">{item.icon}</span>
                                        <span className="nav-label">{item.label}</span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                {llmStatusText && (
                    <>
                        <div className="sidebar-sep" />
                        <div className="sidebar-llm-status" title={llmStatusText}>
                            {llmStatusText}
                        </div>
                    </>
                )}

                <div className="sidebar-user">
                    <div className="username" title={currentUser.username}>{currentUser.username}</div>
                    <div className="role-badge">
                        {currentUser.role === "admin" ? "管理员" : "用户"}
                    </div>
                    <button onClick={onLogout} title="退出登录">
                        <span className="logout-icon">⎋</span>
                        <span className="logout-label">退出登录</span>
                    </button>
                </div>
            </nav>
        </>
    );
}
