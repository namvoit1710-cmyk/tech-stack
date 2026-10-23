import { Checkbox } from "@ldc/ui/components/checkbox";
import { Label } from "@ldc/ui/components/label";
import { forwardRef } from "react";
import type { FieldComponentProps } from "../../types/schema";

export interface CheckBoxControlProps extends FieldComponentProps {
    checked?: boolean;
    onCheckedChange?: (checked: boolean) => void;
}

const CheckBoxControl = forwardRef<HTMLButtonElement, CheckBoxControlProps>((props, ref) => {
    const { field: _field, checked, onCheckedChange, value, onChange, ...rest } = props;

    const isChecked = checked ?? (value === true || value === "true");

    const handleCheckedChange = (checked: boolean) => {
        onCheckedChange?.(checked);
        onChange?.(checked);
    };

    return (
        <div className="flex items-center gap-x-2">
            <Checkbox
                ref={ref}
                checked={isChecked}
                onCheckedChange={handleCheckedChange}
                {...rest}
            />
            <Label className="text-sm font-normal cursor-pointer">{rest.label}</Label>
        </div>
    );
});

CheckBoxControl.displayName = "CheckBoxControl";

export default CheckBoxControl;
