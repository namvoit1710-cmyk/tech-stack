import { Textarea } from "@ldc/ui/components/textarea";
import { forwardRef } from "react";
import type { FieldComponentProps } from "../../types/schema";

const TextareaControl = forwardRef<HTMLTextAreaElement, FieldComponentProps>((props, ref) => {
    const { field: _field, error, ...rest } = props;
    return <Textarea ref={ref} aria-invalid={!!error} {...rest} />;
});

TextareaControl.displayName = "TextareaControl";

export default TextareaControl;