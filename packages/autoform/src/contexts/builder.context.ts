import type { ComponentType } from "react";
import { createContext, useContext } from "react";
import { BuilderServices } from "../hooks/use-builder-services";
import type { FieldComponentProps, FieldWrapperProps, ISchema } from "../types/schema";

export type { ServiceHandler as BuilderServiceHandler, BuilderServices } from "../hooks/use-builder-services";

interface IBuilderContext {
    schema: ISchema;
    fieldComponent: Record<string, ComponentType<FieldComponentProps> | null>;
    wrapperComponent: Record<string, ComponentType<FieldWrapperProps> | null>;

    services?: BuilderServices;

    registerRefetch?: (key: string, fn: () => Promise<void>) => void;
    unregisterRefetch?: (key: string) => void;

    refresh?: boolean;
    setRefresh?: (refresh: boolean) => void;

    readonly?: boolean;

    onFormActions?: (action: string, payload?: Record<string, unknown>) => Promise<void> | void | Record<string, unknown> | null;
}

const BuilderContext = createContext<IBuilderContext | null>(null);

export const BuilderProvider = BuilderContext.Provider;

export const useBuilderContext = () => {
    const context = useContext(BuilderContext);
    if (!context) {
        throw new Error(
            "useBuilderContext must be used within an BuilderProvider"
        );
    }

    return context;
};

/** Non-throwing variant: returns null outside a BuilderProvider (for optional consumers like field-scripts). */
export const useOptionalBuilderContext = () => useContext(BuilderContext);
