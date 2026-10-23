import { cn } from "@ldc/ui"
import { Separator } from "@ldc/ui/components/separator"
import { SidebarTrigger } from "@ldc/ui/components/sidebar"
import type { ReactNode } from "react"

interface IPageContainerProps {
    children: ReactNode
    className?: string
}

interface IPageHeaderProps {
    title: string | ReactNode
    description?: string
    actions?: ReactNode
    className?: string
}

const Header = ({ title, description, actions, className }: IPageHeaderProps) => {
    return (
        <header className={cn("flex justify-between pb-2 pr-4 shrink-0 items-start gap-2 transition-[width,height] ease-linear", className)}>
            <div className="flex items-start gap-2 px-4">
                <SidebarTrigger className="-ml-1" />

                <Separator
                    orientation="vertical"
                    className="mr-2 mt-1.5 data-[orientation=vertical]:h-4"
                />

                <div className="flex flex-col">
                    <h4 className="text-md font-medium">{title}</h4>

                    {description && (
                        <p className="text-sm text-muted-foreground">{description}</p>
                    )}
                </div>
            </div>

            {actions}
        </header>
    )
}

const Root = ({ children, className }: IPageContainerProps) => {
    return (
        <div className={cn("h-full flex flex-col overflow-hidden gap-2 pt-4 border-l border-l-border", className)}>
            {children}
        </div>
    )
}

const Page = Object.assign(Root, { Header })

export default Page