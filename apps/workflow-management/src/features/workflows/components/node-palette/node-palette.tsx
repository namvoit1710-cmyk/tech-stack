import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { ChevronRightIcon, MenuIcon } from "lucide-react";
import { useEffect, useState } from "react";

interface INodePaletteProps {
    children: React.ReactNode;
    isOpen: boolean;
    side?: "left" | "right";
    onClose?: () => void;
    title?: string;
}

const NodePalette = (props: INodePaletteProps) => {

    const { t } = useLanguage()
    const { children, isOpen, onClose, side = "left", title } = props;


    const [isOpened, setIsOpened] = useState(false)

    const handleCloseDrawer = () => {
        setIsOpened(false)
        setTimeout(() => onClose?.(), 100)
    }

    useEffect(() => {
        if (!isOpened) return;

        const handleClickOutside = (event: MouseEvent) => {
            const target = event.target as HTMLElement;
            if (!target) return;

            if (target.closest("#workflow-nodes-drawer")) return;
            if (target.closest("[data-side-toolbar-trigger]")) return;

            handleCloseDrawer()
        }

        window.addEventListener("mousedown", handleClickOutside)

        return () => {
            window.removeEventListener("mousedown", handleClickOutside);
        }
    }, [isOpened, onClose])

    useEffect(() => {
        const timeOut = setTimeout(() => {
            setIsOpened(isOpen)
        }, 0)

        return () => clearTimeout(timeOut)
    }, [isOpen])



    if (!isOpen) {
        return null
    }

    return (
        <section
            id="workflow-nodes-drawer"
            className={cn(
                "w-80 h-full overflow-hidden",
                "absolute top-0 z-20",
                "bg-background border-l border-border",
                "flex flex-col",
                "transition-all duration-300 ease-in-out",
                side === "right" ? "-right-full" : "-left-full",
                isOpened && side === "left" && "left-0",
                isOpened && side === "right" && "right-0"
            )}
        >
            <div className="flex items-center justify-between px-4 py-3 border-b border-b-gray-300">
                <div className="flex items-center gap-2">
                    <MenuIcon className="size-5" strokeWidth={1.5} />
                    <span className="text-md font-medium">{title ?? t("node_library")}</span>
                </div>

                <Button
                    variant="ghost"
                    size="icon"
                    onClick={handleCloseDrawer}
                >
                    <ChevronRightIcon />
                </Button>
            </div>

            <div className="flex-2 overflow-hidden">
                {children}
            </div>
        </section>
    )
}

export default NodePalette