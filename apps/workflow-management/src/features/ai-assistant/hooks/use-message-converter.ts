import type { ThreadMessageLike } from "@assistant-ui/react";
import { IAIAssistantMessage } from "../types";

export const convertMessage = (
    message: IAIAssistantMessage,
): ThreadMessageLike => {
    const content: ThreadMessageLike["content"] = [
        { type: "text", text: message.content },
    ];

    return {
        id: message.id,
        role: message.role,
        content,
        createdAt: new Date(message.createdAt),
    };
};