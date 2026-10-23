"use client"

import {
    BadgeCheck,
    Bell,
    ChevronsUpDown,
    CreditCard,
    LogOut,
    Sparkles,
} from "lucide-react"

import { useLanguage } from "@/hooks/use-language"
import { Avatar, AvatarFallback, AvatarImage } from "@ldc/ui/components/avatar"
import { Badge } from "@ldc/ui/components/badge"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuGroup,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@ldc/ui/components/dropdown-menu"
import {
    SidebarMenu,
    SidebarMenuButton,
    SidebarMenuItem,
    useSidebar,
} from "@ldc/ui/components/sidebar"

// SA-2810: show the user's Keycloak realm roles as text badges (no icon) in the
// account menu header. It renders WHATEVER roles the /me session DTO carries, so
// new realm roles added in Keycloak appear automatically with NO code change.
// Admin-type roles (name contains "admin") get an amber tint; every other role is
// neutral. Empty list → nothing rendered (menu unchanged). Value from useAuth (SA-2762).
function prettifyRole(role: string): string {
    return role
        .replace(/[-_]+/g, " ")
        .trim()
        .replace(/\b\w/g, (ch) => ch.toUpperCase())
}

function RoleBadges({ roles }: { roles: string[] }) {
    const { t } = useLanguage()
    if (!roles.length) return null
    return (
        <>
            {roles.map((role) => {
                const isAdmin = role.toLowerCase().includes("admin")
                const label = prettifyRole(role)
                return (
                    <Badge
                        key={role}
                        variant={isAdmin ? "outline" : "secondary"}
                        className={
                            isAdmin
                                ? "border-amber-300 bg-amber-100 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-400"
                                : undefined
                        }
                        aria-label={`${t("user.role")}: ${label}`}
                    >
                        {label}
                    </Badge>
                )
            })}
        </>
    )
}

export function NavUser({
    user,
    onLogout,
}: {
    user: {
        name: string
        email: string
        avatar: string
        roles: string[]
    }
    onLogout: () => void
}) {
    const { isMobile } = useSidebar()
    const { t } = useLanguage()
    const initials = (user.name || user.email || "User")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((segment) => segment[0]?.toUpperCase() ?? "")
        .join("") || "U"

    return (
        <SidebarMenu>
            <SidebarMenuItem>
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <div>
                            <SidebarMenuButton
                                size="lg"
                                className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
                            >
                                <Avatar className="h-8 w-8 rounded-lg">
                                    <AvatarImage src={user.avatar} alt={user.name} />
                                    <AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
                                </Avatar>
                                <div className="grid flex-1 text-left text-sm leading-tight">
                                    <span className="truncate font-medium">{user.name}</span>
                                    <span className="truncate text-xs">{user.email}</span>
                                </div>
                                <ChevronsUpDown className="ml-auto size-4" />
                            </SidebarMenuButton>
                        </div>
                    </DropdownMenuTrigger>

                    <DropdownMenuContent
                        className="w-(--radix-dropdown-menu-trigger-width) min-w-64 rounded-lg"
                        side={isMobile ? "bottom" : "right"}
                        align="end"
                        sideOffset={4}
                    >
                        <DropdownMenuLabel className="p-0 font-normal">
                            <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
                                <Avatar className="h-8 w-8 rounded-lg">
                                    <AvatarImage src={user.avatar} alt={user.name} />
                                    <AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
                                </Avatar>
                                <div className="grid flex-1 text-left text-sm leading-tight">
                                    <span className="flex flex-wrap items-center gap-1.5 font-medium">
                                        <span className="truncate">{user.name}</span>
                                        <RoleBadges roles={user.roles} />
                                    </span>
                                    <span className="truncate text-xs">{user.email}</span>
                                </div>
                            </div>
                        </DropdownMenuLabel>
                        <DropdownMenuSeparator />
                        <DropdownMenuGroup>
                            <DropdownMenuItem>
                                <Sparkles />
                                Upgrade to Pro
                            </DropdownMenuItem>
                        </DropdownMenuGroup>
                        <DropdownMenuSeparator />
                        <DropdownMenuGroup>
                            <DropdownMenuItem>
                                <BadgeCheck />
                                {t("user.account")}
                            </DropdownMenuItem>
                            <DropdownMenuItem>
                                <CreditCard />
                                {t("user.billing")}
                            </DropdownMenuItem>
                            <DropdownMenuItem>
                                <Bell />
                                {t("user.notifications")}
                            </DropdownMenuItem>
                        </DropdownMenuGroup>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                            variant="destructive"
                            onClick={onLogout}
                        >
                            <LogOut />
                            {t("user.logout")}
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
            </SidebarMenuItem>
        </SidebarMenu>
    )
}
