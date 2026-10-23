import type { MFUser } from "@ldc/mf-auth";
import { useMFAuth } from "@ldc/mf-auth";
import type { ReactNode } from "react";
import { createContext, useContext, useMemo } from "react";
import { BFF_MODE, useBffAuth } from "@/hooks/use-bff-auth";

export interface AuthContextValue {
    displayName?: string;
    isAuthenticated?: boolean;
    isLoading?: boolean;
    permissions?: string[];
    roles?: string[];
    isAdmin?: boolean;
    user?: MFUser | null;
    userEmail?: string | null;
    userId?: string | null;
    logout?: (reason?: string) => void;
    hasPermission?: (permission?: string) => boolean;
    hasRole?: (role?: string) => boolean;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export interface AuthUser {
    id: string;
    email?: string;
    name?: string;
    roles?: string[];
    [key: string]: unknown;
}

function getDisplayName(user: MFUser | null) {
    return user?.name ?? user?.email ?? "User";
}

export function AuthProvider({ children }: { children: ReactNode }) {
    // Both hooks run every render (rules of hooks); useBffAuth only fetches when
    // BFF_MODE is on. In standalone BFF mode the session comes from the
    // control-plane Keycloak BFF; otherwise it comes from the shell via mf-auth.
    const mfShareAuth = useMFAuth();
    const bffAuth = useBffAuth(BFF_MODE);
    const source = (BFF_MODE ? bffAuth : mfShareAuth) ?? {};
    const { user, isAuthenticated, permissions, logout, isLoading } = source as {
        user?: MFUser | null;
        isAuthenticated?: boolean;
        isLoading?: boolean;
        permissions?: string[];
        logout?: (reason?: string) => void;
    };

    const value = useMemo<AuthContextValue>(() => {
        // BFF /me (and the shell) return the user's Keycloak roles; MFUser doesn't
        // type them, so read them off the user object. admin = any *admin role.
        const roles = ((user as { roles?: string[] } | null | undefined)?.roles) ?? [];
        const isAdmin = roles.some((r) => String(r).toLowerCase().includes("admin"));
        return {
            displayName: getDisplayName(user ?? null),
            isAuthenticated,
            isLoading,
            logout,
            permissions,
            roles,
            isAdmin,
            hasRole: (role?: string) => !!role && roles.includes(role),
            user,
            userEmail: user?.email ?? null,
            userId: user?.id ?? null,
        };
    }, [isAuthenticated, isLoading, logout, permissions, user]);

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
    const context = useContext(AuthContext);

    if (!context) {
        throw new Error("useAuth must be used within AuthProvider");
    }

    return context;
}