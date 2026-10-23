import type { ReactNode } from "react";
import { useEffect } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import { BFF_MODE, bffLoginRedirect } from "@/hooks/use-bff-auth";

// SA-2532 — gate app rendering on the BFF session (standalone Keycloak mode).
// When not signed in — a first visit, or after a Single Log-Out that ended the
// Keycloak session — go straight to the Keycloak login page ("Sign in to
// Tenant-1"). No intermediate app screen. In legacy mf-auth mode this is a
// no-op pass-through.
export function BffAuthGate({ children }: { children: ReactNode }) {
    if (!BFF_MODE) return <>{children}</>;
    return <BffGate>{children}</BffGate>;
}

function BffGate({ children }: { children: ReactNode }) {
    const { isAuthenticated, isLoading } = useAuth();

    useEffect(() => {
        if (!isLoading && !isAuthenticated) {
            bffLoginRedirect();
        }
    }, [isLoading, isAuthenticated]);

    if (isAuthenticated) return <>{children}</>;

    return (
        <div className="flex min-h-screen w-full flex-col items-center justify-center gap-4 text-muted-foreground">
            {isLoading ? "Loading…" : "Redirecting to sign-in…"}
        </div>
    );
}
