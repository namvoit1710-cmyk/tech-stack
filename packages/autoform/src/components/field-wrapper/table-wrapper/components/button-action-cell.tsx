import { Button } from "@ldc/ui/components/button";
import { memo } from "react";
import type { IButtonAction } from "../../../../types/schema";

interface IButtonTableActionProps {
    row: any;
    buttonAction?: IButtonAction & { actionFunc?: (rest: Omit<IButtonAction, "actionFunc">, row: any) => void };
}

const ButtonTableAction = memo(({ row, buttonAction }: IButtonTableActionProps) => {
    const { label, actionFunc, ...rest } = buttonAction ?? {};

    return (
        <Button
            variant="outline"
            className="max-w-[100px] truncate"
            onClick={() => actionFunc?.(rest, row)}
        >
            {label ?? "Action"}
        </Button>
    )
});

ButtonTableAction.displayName = "ButtonTableAction";

export default ButtonTableAction;
