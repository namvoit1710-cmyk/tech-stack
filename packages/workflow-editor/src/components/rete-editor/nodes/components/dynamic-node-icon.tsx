import { EthernetPort } from "lucide-react";
import type { IconName } from "lucide-react/dynamic";
import { DynamicIcon } from "lucide-react/dynamic";
import type { ComponentProps } from "react";
import { memo } from "react";
import { pascalToKebabCase } from "../../../../utils/string-utils";

interface IDynamicNodeIconProps extends Omit<ComponentProps<typeof DynamicIcon>, "name" | "strokeWidth"> {
    name: string
    strokeWidth?: number
    fallbackIconName?: string
}

const DynamicNodeIcon = ({ name, strokeWidth, fallbackIconName, fallback, ...props }: IDynamicNodeIconProps) => {
    const iconName = name
        ? (pascalToKebabCase(name) as IconName)
        : ((fallbackIconName as IconName) ?? "ethernet-port");

    return (
        <DynamicIcon
            {...props}
            name={iconName}
            strokeWidth={strokeWidth ?? 1}
            fallback={fallback ?? (() => <EthernetPort  {...props} strokeWidth={strokeWidth ?? 1} />)}
        />
    )
}

const memoizedDynamicNodeIcon = (prev: IDynamicNodeIconProps, next: IDynamicNodeIconProps) =>
    prev.name === next.name &&
    prev.strokeWidth === next.strokeWidth &&
    prev.fallbackIconName === next.fallbackIconName &&
    prev.className === next.className &&
    prev.style === next.style

export default memo(DynamicNodeIcon, memoizedDynamicNodeIcon)