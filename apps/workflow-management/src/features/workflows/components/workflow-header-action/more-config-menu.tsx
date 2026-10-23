import { useLanguage } from "@/hooks/use-language";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@ldc/ui/components/dropdown-menu";
import { Clock, Cpu, Edit, FileText, Settings, UserCog } from "lucide-react";
import { useMemo } from "react";

export enum MenuActionEnum {
    ReplaceName = "replace-name",
    ReplaceDescription = "replace-description",
    SettingRoutingPath = "setting-routing-path",
    // SA-2484: worker-instance selection (debug routing) is an advanced config —
    // lives here in the ⋯ menu instead of a top-level toolbar button.
    SelectWorkerInstance = "select-worker-instance",
    // SA-2570: reassign the workflow owner (opens the owner-picker modal).
    ChooseOwner = "choose-owner",
    // SA-2751: set/edit the manual time this flow saves (opens the clock dialog).
    SetManualTime = "set-manual-time",
}

interface IMenuConfigSettingProps {
    onAction?: (action: MenuActionEnum) => void;
    children: React.ReactNode;
}

const MenuConfigSetting = ({ onAction, children }: IMenuConfigSettingProps) => {
    const { t } = useLanguage();

    const menus = useMemo(() => ([
        {
            id: MenuActionEnum.ReplaceName,
            label: t("replace_name"),
            icon: <Edit />
        },
        {
            id: MenuActionEnum.ReplaceDescription,
            label: t("replace_workflow_description"),
            icon: <FileText />
        },
        {
            id: MenuActionEnum.SettingRoutingPath,
            label: t("setting_routing_path"),
            icon: <Settings />
        },
        {
            id: MenuActionEnum.SelectWorkerInstance,
            label: "Select worker instance",
            icon: <Cpu />
        },
        {
            id: MenuActionEnum.ChooseOwner,
            label: t("choose_owner"),
            icon: <UserCog />
        },
        {
            id: MenuActionEnum.SetManualTime,
            label: "Set manual time",
            icon: <Clock />
        }
    ]), [])

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                {children}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-64">
                {menus.map(item => (
                    <DropdownMenuItem
                        key={item.id}
                        onClick={() => onAction?.(item.id)}
                    >
                        {item.icon}
                        {item.label}
                    </DropdownMenuItem>
                ))}
            </DropdownMenuContent>
        </DropdownMenu>
    )
}

export default MenuConfigSetting
