import { useTranslation } from "@ldc/i18n";
import { Button } from "@ldc/ui/components/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { Clipboard, CopyIcon, LoaderCircle, PlusIcon } from "lucide-react";
import { useMemo } from "react";
import type { handlerLoadingType } from "../../../hooks/use-editor-clipboard";
import { RETE_EDITOR_I18N_NAMESPACE } from "../../../i18n";

export interface IExtension {
    icon: React.ReactNode;
    label: string;
    callback: () => void;
    key: string;
}

interface ISideToolBarProps {
    isHandlerLoading?: handlerLoadingType
    clipboardHandlers: {
        onCopy: () => Promise<void>;
        onPaste: () => Promise<void>;
    }
    extensions?: IExtension[];
    onAddNode?: () => void;
}

const SideToolBar = ({ clipboardHandlers, isHandlerLoading, extensions, onAddNode }: ISideToolBarProps) => {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

    const menuItems = useMemo(() => {
        return [
            {
                icon: <PlusIcon />,
                label: t("toolbar.add_node"),
                onClick: onAddNode,
            },
            {
                icon: isHandlerLoading?.isCopyLoading ? <LoaderCircle className="animate-spin text-blue-500" /> : <CopyIcon />,
                label: t("toolbar.copy"),
                onClick: clipboardHandlers.onCopy,
            },
            {
                icon: isHandlerLoading?.isPasteLoading ? <LoaderCircle className="animate-spin text-blue-500" /> : <Clipboard />,
                label: t("toolbar.paste"),
                onClick: clipboardHandlers.onPaste,
            },
        ]
    }, [t, onAddNode, clipboardHandlers, isHandlerLoading])

    return (
        <TooltipProvider>
            <div className="absolute right-4 top-4 z-20">
                <div className="flex items-center flex-col gap-2">
                    {menuItems.map((item) => (
                        <Tooltip key={item.label}>
                            <TooltipTrigger asChild>
                                <Button
                                    size="icon-lg"
                                    variant="outline"
                                    className="shadow-lg cursor-pointer"
                                    onClick={item.onClick}
                                    aria-label={item.label}
                                >
                                    {item.icon}
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent side="left">
                                {item.label}
                            </TooltipContent>
                        </Tooltip>
                    ))}

                    {extensions?.map(item => (
                        <Tooltip key={item.key}>
                            <TooltipTrigger asChild>
                                <Button
                                    size="icon-lg"
                                    variant="outline"
                                    className="shadow-lg cursor-pointer"
                                    onClick={item.callback}
                                    aria-label={item.label}
                                >
                                    {item.icon}
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                                <>
                                    <div>{item.label}</div>
                                </>
                            </TooltipContent>
                        </Tooltip>
                    ))}
                </div>
            </div>
        </TooltipProvider>
    )
}

export default SideToolBar
