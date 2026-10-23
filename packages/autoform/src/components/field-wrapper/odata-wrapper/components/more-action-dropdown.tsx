import { Button } from "@ldc/ui/components/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@ldc/ui/components/dropdown-menu";
import { MoreHorizontal } from "lucide-react";
import { Slot, useSlots } from "../../../../contexts/slot.context";

const MoreActionDropdown = () => {
    const { slots } = useSlots();
    const moreAction = slots["more-action"];

    if (!moreAction?.length) return null;

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm">
                    <MoreHorizontal />
                </Button>
            </DropdownMenuTrigger>

            <DropdownMenuContent align="end" side="bottom" className="">
                <Slot name="more-action" />
            </DropdownMenuContent>
        </DropdownMenu>
    )
}

export default MoreActionDropdown;