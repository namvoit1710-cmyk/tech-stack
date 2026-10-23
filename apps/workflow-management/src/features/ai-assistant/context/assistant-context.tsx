import {
    createContext,
    useCallback,
    useContext,
    useMemo,
    type ReactNode,
} from "react";
import { IAIAssistantMessage } from "../types";

interface AIAssistantContextValue {
    messages: IAIAssistantMessage[];
    getMessageById: (id: string) => IAIAssistantMessage | undefined;
}

const AIAssistantContext = createContext<AIAssistantContextValue | null>(null);

interface AIAssistantProviderProps {
    messages: IAIAssistantMessage[];
    children: ReactNode;
}

export const AIAssistantProvider = ({
    messages,
    children,
}: AIAssistantProviderProps) => {
    const getMessageById = useCallback(
        (id: string) => messages.find((m) => m.id === id),
        [messages],
    );

    const value = useMemo(
        () => ({ messages, getMessageById }),
        [messages, getMessageById],
    );

    return (
        <AIAssistantContext.Provider value={value}>
            {children}
        </AIAssistantContext.Provider>
    );
};

export const useAIAssistantContext = (): AIAssistantContextValue => {
    const context = useContext(AIAssistantContext);
    if (!context) {
        throw new Error(
            "useAIAssistantContext must be used within AIAssistantProvider",
        );
    }
    return context;
};