import { env } from "@/env";
import { useQuery } from "@ldc/tanstack-query";
import { useCallback } from "react";

// SA-2532 — standalone Keycloak BFF auth. When PUBLIC_AUTH_MODE === "bff" the
// workflow-management app authenticates itself against the control-plane BFF
// (/api/v1/auth/*) instead of receiving a session from the shell via mf-auth.
export const BFF_MODE = env.PUBLIC_AUTH_MODE === "bff";

const AUTH_BASE = `${env.PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE}/api/v1/auth`;

export interface BffUser {
    id: string;
    email?: string | null;
    name?: string | null;
    roles?: string[];
}

/** Send the browser to the BFF login, which redirects to the Keycloak login page. */
export function bffLoginRedirect(): void {
    const redirect = encodeURIComponent(window.location.href);
    window.location.href = `${AUTH_BASE}/login?redirect=${redirect}`;
}

async function fetchMe(): Promise<BffUser> {
    const res = await fetch(`${AUTH_BASE}/me`, { credentials: "include" });
    if (res.status === 401) {
        // Not signed in. Don't redirect here — the auth gate decides whether to
        // start login (first visit) or show the signed-out screen (post logout).
        throw new Error("unauthenticated");
    }
    if (!res.ok) throw new Error(`auth/me ${res.status}`);
    return res.json();
}

/**
 * Resolve the current user from the BFF. Mirrors the shape the AuthProvider
 * expects from mf-auth ({ user, isAuthenticated, permissions, logout }).
 */
export function useBffAuth(enabled: boolean) {
    const query = useQuery({
        queryKey: ["bff", "me"],
        queryFn: fetchMe,
        enabled,
        retry: false,
        staleTime: Infinity,
    });

    const logout = useCallback(async () => {
        // Single Log-Out (SLO): clear the BFF session, then navigate to the
        // Keycloak end-session URL (id_token_hint + post_logout_redirect) so the
        // Keycloak session — and, via the IdP, the Azure session — is ended too.
        // Keycloak redirects back to AUTH_POST_LOGOUT_REDIRECT (the app); the
        // auth gate then sees no session and sends the browser straight to the
        // Keycloak login page. Falls back to "/" if no end-session URL.
        let target = "/";
        try {
            const res = await fetch(`${AUTH_BASE}/logout`, { method: "POST", credentials: "include" });
            const data = (await res.json()) as { logout_url?: string };
            if (data?.logout_url) target = data.logout_url;
        } catch {
            /* best effort — the server session/cookie clear is what matters */
        }
        window.location.href = target;
    }, []);

    const user = (query.data as BffUser | undefined) ?? null;
    return {
        user,
        isAuthenticated: !!user,
        permissions: user?.roles ?? [],
        logout,
        isLoading: query.isLoading,
    };
}
