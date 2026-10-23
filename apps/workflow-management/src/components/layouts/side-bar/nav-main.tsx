"use client"

import { ChevronRight, type LucideIcon } from "lucide-react"
import { useState } from "react"

import { useLanguage } from "@/hooks/use-language"
import {
    Collapsible,
    CollapsibleContent,
} from "@ldc/ui/components/collapsible"
import {
    SidebarGroup,
    SidebarMenu,
    SidebarMenuButton,
    SidebarMenuItem,
    SidebarMenuSub,
    SidebarMenuSubButton,
    SidebarMenuSubItem,
    useSidebar,
} from "@ldc/ui/components/sidebar"
import { Link, useLocation } from "react-router-dom"

type NavItem = {
    title: string
    url: string
    icon?: LucideIcon
    isActive?: boolean
    items?: { title: string; url: string }[]
}

// SA-2616: a collapsible group (Dashboard, Documentations, …) must expand on click.
// When the rail is collapsed (icon-only) its sub-menu is hidden, so a click first
// expands the rail, then opens the group — otherwise nothing appears to happen.
function CollapsibleNavItem({ item }: { item: NavItem }) {
    const { t } = useLanguage()
    const { pathname } = useLocation()
    const { state, isMobile, setOpen: setSidebarOpen, setOpenMobile } = useSidebar()
    const [open, setOpen] = useState(!!item.isActive)

    const handleToggle = () => {
        if (state === "collapsed" && !isMobile) {
            // icon rail → open the rail so the sub-items are visible, and open the group
            setSidebarOpen(true)
            setOpen(true)
            return
        }
        if (isMobile) setOpenMobile(true)
        setOpen((o) => !o)
    }

    return (
        <Collapsible asChild open={open} onOpenChange={setOpen} className="group/collapsible">
            <SidebarMenuItem>
                <SidebarMenuButton tooltip={t(item.title)} onClick={handleToggle}>
                    {item.icon && <item.icon />}
                    <span>{t(item.title)}</span>
                    <ChevronRight className="ml-auto transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90" />
                </SidebarMenuButton>

                <CollapsibleContent>
                    <SidebarMenuSub>
                        {item.items?.map((subItem) => (
                            <SidebarMenuSubItem key={subItem.title}>
                                <SidebarMenuSubButton asChild isActive={pathname === subItem.url}>
                                    <Link to={subItem.url}>
                                        <span>{t(subItem.title)}</span>
                                    </Link>
                                </SidebarMenuSubButton>
                            </SidebarMenuSubItem>
                        ))}
                    </SidebarMenuSub>
                </CollapsibleContent>
            </SidebarMenuItem>
        </Collapsible>
    )
}

export function NavMain({ items }: { items: NavItem[] }) {
    const { t } = useLanguage()
    const { pathname } = useLocation()

    return (
        <SidebarGroup>
            <SidebarMenu>
                {items.map((item) =>
                    item.items?.length ? (
                        <CollapsibleNavItem key={item.title} item={item} />
                    ) : (
                        <SidebarMenuItem key={item.title}>
                            <Link to={item.url}>
                                <SidebarMenuButton tooltip={t(item.title)} isActive={pathname === item.url}>
                                    {item.icon && <item.icon />}
                                    <span>{t(item.title)}</span>
                                </SidebarMenuButton>
                            </Link>
                        </SidebarMenuItem>
                    )
                )}
            </SidebarMenu>
        </SidebarGroup>
    )
}
