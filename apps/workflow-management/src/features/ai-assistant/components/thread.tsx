
import { useLanguage } from "@/hooks/use-language";
import {
    ComposerPrimitive,
    MessagePrimitive,
    ThreadPrimitive,
    useAuiState,
} from "@assistant-ui/react";
import { ArrowDownIcon, ArrowUpIcon, Sparkles, SquareIcon } from "lucide-react";
import type { FC } from "react";
import { AssistantMessage } from "./assistant-message";
import { TooltipIconButton } from "./tooltip-icon-button";

export const AIAssistantThread: FC = () => {
    return (
        <ThreadPrimitive.Root
            className="aui-root aui-thread-root flex h-full flex-col"
            style={{
                ["--thread-max-width" as string]: "100%",
                // SA-1582: softer, rounder composer for the claymorphism look.
                ["--composer-radius" as string]: "20px",
                ["--composer-padding" as string]: "8px",
            }}
        >
            <ThreadPrimitive.Viewport
                turnAnchor="bottom"
                className="aui-thread-viewport relative flex flex-1 flex-col overflow-y-auto scroll-smooth px-4 pt-4"
            >
                <ThreadPrimitive.Empty>
                    <ThreadWelcome />
                </ThreadPrimitive.Empty>

                <ThreadPrimitive.Messages>
                    {() => <ThreadMessage />}
                </ThreadPrimitive.Messages>

                <ThreadPrimitive.ViewportFooter className="aui-thread-viewport-footer sticky bottom-0 mt-auto flex w-full flex-col gap-2 bg-background pb-4">
                    <ThreadScrollToBottom />
                    <AIAssistantComposer />
                </ThreadPrimitive.ViewportFooter>
            </ThreadPrimitive.Viewport>
        </ThreadPrimitive.Root>
    );
};

const ThreadMessage: FC = () => {
    const role = useAuiState((s) => s.message.role);

    if (role === "user") return <UserMessage />;
    return <AssistantMessage />;
};

const ThreadWelcome: FC = () => {
    const { t } = useLanguage();

    return (
        <div className="mx-auto my-auto flex w-full max-w-3xl grow flex-col items-center justify-center gap-8 px-4 py-8">
            <div className="flex flex-col items-center gap-4 text-center">
                <div className="rounded-[28px] bg-primary/10 p-4 text-primary shadow-[inset_0_1px_2px_rgba(255,255,255,0.6),0_8px_20px_-8px_rgba(15,23,42,0.3)]">
                    <Sparkles className="size-8" />
                </div>
                <div className="flex flex-col gap-2">
                    <h2 className="text-2xl font-semibold tracking-tight">
                        {t("ai_workflow_welcome_title") || "AI Workflow Assistant"}
                    </h2>
                    <p className="text-muted-foreground text-base">
                        {t("ai_chatbot_welcome_message") ||
                            "Describe the workflow you want to create, and I'll help you build it step by step."}
                    </p>
                </div>
            </div>
        </div>
    );
};

const UserMessage: FC = () => {
    return (
        <MessagePrimitive.Root
            className="fade-in slide-in-from-bottom-1 mx-auto flex w-full max-w-(--thread-max-width) animate-in justify-end py-2 duration-150"
            data-role="user"
        >
            <div className="max-w-[80%] rounded-3xl rounded-br-lg bg-primary px-4 py-2.5 text-primary-foreground shadow-[0_6px_16px_-6px_rgba(15,23,42,0.35),inset_0_1px_1px_rgba(255,255,255,0.25)]">
                <MessagePrimitive.Parts />
            </div>
        </MessagePrimitive.Root>
    );
};

const AIAssistantComposer: FC = () => {
    const { t } = useLanguage();

    return (
        <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
            <div className="flex w-full flex-col gap-2 rounded-(--composer-radius) border border-border/60 bg-background/80 p-(--composer-padding) shadow-[0_8px_24px_-12px_rgba(15,23,42,0.25),inset_0_1px_2px_rgba(255,255,255,0.7)] backdrop-blur-sm transition-shadow focus-within:border-ring/75 focus-within:ring-2 focus-within:ring-ring/20">
                <ComposerPrimitive.Input
                    placeholder={
                        t("ai_chatbot_input_placeholder") ||
                        "Describe your workflow..."
                    }
                    className="aui-composer-input max-h-32 min-h-10 w-full resize-none bg-transparent px-2 py-1 text-sm outline-none placeholder:text-muted-foreground/80"
                    rows={3}
                    autoFocus
                />
                <div className="flex items-center justify-between">
                    <p className="text-xs text-muted-foreground px-1">
                        {t("workflow_prompt_hint") || ""}
                    </p>
                    <ComposerActions />
                </div>
            </div>
        </ComposerPrimitive.Root>
    );
};

const ComposerActions: FC = () => {
    return (
        <>
            <ThreadPrimitive.If running={false}>
                <ComposerPrimitive.Send asChild>
                    <TooltipIconButton
                        tooltip="Send"
                        variant="default"
                        size="icon"
                        className="size-8 rounded-full"
                    >
                        <ArrowUpIcon className="size-4" />
                    </TooltipIconButton>
                </ComposerPrimitive.Send>
            </ThreadPrimitive.If>
            <ThreadPrimitive.If running>
                <ComposerPrimitive.Cancel asChild>
                    <TooltipIconButton
                        tooltip="Stop"
                        variant="default"
                        size="icon"
                        className="size-8 rounded-full"
                    >
                        <SquareIcon className="size-3 fill-current" />
                    </TooltipIconButton>
                </ComposerPrimitive.Cancel>
            </ThreadPrimitive.If>
        </>
    );
};

const ThreadScrollToBottom: FC = () => {
    return (
        <ThreadPrimitive.ScrollToBottom asChild>
            <TooltipIconButton
                tooltip="Scroll to bottom"
                variant="outline"
                className="absolute -top-10 z-10 self-center rounded-full disabled:invisible"
            >
                <ArrowDownIcon />
            </TooltipIconButton>
        </ThreadPrimitive.ScrollToBottom>
    );
};