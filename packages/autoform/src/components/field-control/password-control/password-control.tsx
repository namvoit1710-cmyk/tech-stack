import { cn } from "@ldc/ui";
import { Input } from "@ldc/ui/components/input";
import { Eye, EyeOff } from "lucide-react";
import { forwardRef, useState } from "react";
import type { FieldComponentProps } from "../../../types/schema";

export interface PasswordControlProps extends FieldComponentProps {
    error?: string | boolean | { message?: string };
    disabled?: boolean;
    className?: string;
}

const PasswordControl = forwardRef<HTMLInputElement, PasswordControlProps>((props, ref) => {
    const { field: _field, error, disabled, className, ...rest } = props;
    const [showPassword, setShowPassword] = useState(false);

    const handleToggle = () => setShowPassword((prev) => !prev);

    return (
        <div className="relative w-full">
            <Input
                {...rest}
                ref={ref}
                type={showPassword ? "text" : "password"}
                aria-invalid={!!error}
                disabled={disabled}
                // Merge, don't replace: a user className must survive alongside the toggle's pr-10.
                className={cn("pr-10", className)}
            />
            <button
                type="button"
                tabIndex={-1}
                aria-label={showPassword ? "Hide password" : "Show password"}
                disabled={disabled}
                onClick={handleToggle}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
            >
                {showPassword ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
            </button>
        </div>
    );
});

PasswordControl.displayName = "PasswordControl";

export default PasswordControl;
