import {
    BookOpen,
    Globe,
    KeyRound,
    LayoutDashboard,
    Network,
    Server,
    Settings2,
    SplinePointer,
    Workflow,
    Zap
} from "lucide-react"
import * as React from "react"

import { NavMain } from "@/components/layouts/side-bar/nav-main"
import { NavUser } from "@/components/layouts/side-bar/nav-user"
import { TeamSwitcher } from "@/components/layouts/side-bar/team-switcher"
import { ThemeModeToggle } from "@/components/layouts/side-bar/theme-mode-toggle"
import { useAuth } from "@/components/providers/auth-provider"
import {
    Sidebar,
    SidebarContent,
    SidebarFooter,
    SidebarHeader,
    SidebarRail,
} from "@ldc/ui/components/sidebar"

// SA-2810: /me returns product roles mixed with Keycloak plumbing — its built-in
// realm roles, the `account` client roles, and permission scopes (resource:action).
// Filter that noise so only real, assignable product roles show as badges. All the
// excluded names are stable Keycloak/permission conventions, so adding a NEW product
// role (e.g. "approver", "data-steward") never touches this code — it just appears.
function isBuiltinRole(role: string): boolean {
    const r = role.toLowerCase()
    return (
        r.startsWith("default-roles-") ||
        r === "offline_access" ||
        r === "uma_authorization" ||
        r === "account" ||
        r === "manage-account" ||
        r === "manage-account-links" ||
        r === "view-profile" ||
        r.includes(":") // permission scopes like "run:read", not roles
    )
}

const data = {
    navMain: [
        {
            title: "workflows",
            url: "/",
            icon: Workflow,
        },
        {
            title: "node_definitions.tab_title",
            url: "/node-definitions",
            icon: SplinePointer,
        },
        {
            title: "workers.tab_title",
            url: "/workers",
            icon: Server,
        },
        {
            title: "gateway.tab_title",
            url: "/gateway/apps",
            icon: Network,
        },
        {
            title: "credentials_settings.title",
            url: "/credentials",
            icon: KeyRound,
        },
        {
            title: "environments.title",
            url: "/environments",
            icon: Globe,
        },
        {
            title: "Triggers",
            url: "/triggers",
            icon: Zap,
        },
        {
            title: "dashboard.title",
            url: "#",
            icon: LayoutDashboard,
            isActive: true,
            items: [
                { title: "dashboard.overview", url: "/dashboard/overview" },
                { title: "dashboard.workflow", url: "/dashboard/workflow" },
                { title: "dashboard.worker", url: "/dashboard/worker" },
            ],
        },
        {
            title: "documentations",
            url: "#",
            icon: BookOpen,
            isActive: true,
            items: [
                {
                    title: "introduction",
                    url: "#",
                },
                {
                    title: "get_started",
                    url: "#",
                },
                {
                    title: "tutorials",
                    url: "#",
                },
                {
                    title: "changelog",
                    url: "#",
                },
            ],
        },
        {
            title: "settings",
            url: "/settings",
            icon: Settings2,
        },
    ]
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
    const { logout, user, isAdmin, roles } = useAuth()
    // SA-2810: surface the user's roles in the account menu. Keycloak has no
    // "becoming admin drops your other roles" mechanism, so an admin usually also
    // carries viewer/other roles. Product rule: if the user is an admin, show ONLY
    // the admin role (their elevated status is what matters); otherwise show every
    // role /me returned, minus Keycloak plumbing. New realm roles appear with NO
    // code change. Empty → badge hidden.
    const displayRoles = React.useMemo(() => {
        const meaningful = (roles ?? []).filter((r) => !isBuiltinRole(r))
        const adminRole = meaningful.find((r) => r.toLowerCase().includes("admin"))
        return adminRole ? [adminRole] : meaningful
    }, [roles])
    const navUser = user
        ? {
            name: user.name || user.email || "User",
            email: user.email || "",
            avatar: "",
            roles: displayRoles,
        }
        : null

    // SA-2798: the Credentials tab is served by a separate service that isn't
    // role-gated yet (SA-2797) — hide it from non-admins so it isn't exposed.
    // Cosmetic only: real enforcement lands with SA-2797 on the backend.
    const navMain = React.useMemo(
        () => data.navMain.filter((item) => item.url !== "/credentials" || isAdmin),
        [isAdmin],
    )

    return (
        <Sidebar collapsible="icon" {...props}>
            <SidebarHeader>
                <TeamSwitcher />
            </SidebarHeader>

            <SidebarContent>
                <NavMain items={navMain} />
            </SidebarContent>

            <SidebarFooter>
                <div className="px-1 pb-1 group-data-[collapsible=icon]:hidden">
                    <ThemeModeToggle />
                </div>
                {navUser ? (
                    <NavUser
                        user={navUser}
                        onLogout={() => logout("workflow-management:sidebar")}
                    />
                ) : null}
            </SidebarFooter>
            <SidebarRail />
        </Sidebar>
    )
}
