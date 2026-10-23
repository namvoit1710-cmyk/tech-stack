import { SidebarInset, SidebarProvider } from "@ldc/ui/components/sidebar";
import { Outlet } from "react-router-dom";
import { AppSidebar } from "./side-bar/app-sidebar";

const MainLayout = () => {
    return (
        <SidebarProvider defaultOpen={false} className="w-screen h-screen overflow-hidden">
            <AppSidebar />

            <SidebarInset className="h-screen overflow-hidden">
                <Outlet />
            </SidebarInset>
        </SidebarProvider>
    )
}

export default MainLayout;
