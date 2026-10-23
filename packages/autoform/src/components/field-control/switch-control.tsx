import { Label } from "@ldc/ui/components/label";
import { Switch } from "@ldc/ui/components/switch";
import { forwardRef } from "react";
import type { FieldComponentProps } from "../../types/schema";

export interface SwitchControlProps extends FieldComponentProps {
    checked?: boolean;
    onCheckedChange?: (checked: boolean) => void;
}

const SwitchControl = forwardRef<HTMLButtonElement, SwitchControlProps>((props, ref) => {
    const { field: _field, checked, onCheckedChange, value, onChange, ...rest } = props;

    const isChecked = checked ?? (value === true || value === "true");

    const handleCheckedChange = (checked: boolean) => {
        onCheckedChange?.(checked);
        onChange?.(checked);
    };

    return (
        <div className="flex items-center gap-x-2">
            <Switch ref={ref} checked={isChecked} onCheckedChange={handleCheckedChange} {...rest} />
            <Label className="text-sm font-normal cursor-pointer">{rest.label}</Label>
        </div>
    );
});

SwitchControl.displayName = "SwitchControl";

export default SwitchControl;
