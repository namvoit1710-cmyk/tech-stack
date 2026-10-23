
import {
    useExternalStoreRuntime,
    type AppendMessage,
} from "@assistant-ui/react";
import { useCallback } from "react";
import { useExecuteAIAssistant } from "./use-execute-ai-asssistant";
import { convertMessage } from "./use-message-converter";

interface UseAIAssistantRuntimeOptions {
    convId: string;
    onWorkflowCreated?: (workflowId: string) => void;
}

export const useAIAssistantRuntime = ({
    convId,
    onWorkflowCreated,
}: UseAIAssistantRuntimeOptions) => {
    const { messages, isRunning, sendMessage } = useExecuteAIAssistant({
        convId,
        onWorkflowCreated,
    });

    const onNew = useCallback(
        async (appendMessage: AppendMessage) => {
            const textPart = appendMessage.content.find(
                (c) => c.type === "text",
            );
            if (!textPart || textPart.type !== "text") return;

            await sendMessage(textPart.text);
        },
        [sendMessage],
    );

    const runtime = useExternalStoreRuntime({
        messages,
        convertMessage,
        isRunning,
        onNew,
    });

    return { runtime, messages };
};