"use client";

import { ComponentPropsWithRef, forwardRef } from "react";

import { cn, Slottable } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@ldc/ui/components/tooltip";

export type TooltipIconButtonProps = ComponentPropsWithRef<typeof Button> & {
    tooltip: string;
    side?: "top" | "bottom" | "left" | "right";
};

export const TooltipIconButton = forwardRef<
    HTMLButtonElement,
    TooltipIconButtonProps
>(({ children, tooltip, side = "bottom", className, ...rest }, ref) => {
    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>
                    <Button
                        variant="ghost"
                        size="icon"
                        {...rest}
                        className={cn("aui-button-icon size-6 p-1", className)}
                        ref={ref}
                    >
                        <Slottable>{children}</Slottable>
                        <span className="aui-sr-only sr-only">{tooltip}</span>
                    </Button>
                </TooltipTrigger>
                <TooltipContent side={side}>{tooltip}</TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
});

TooltipIconButton.displayName = "TooltipIconButton";
