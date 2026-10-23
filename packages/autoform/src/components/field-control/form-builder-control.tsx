import { Button } from "@ldc/ui/components/button";
import { ChevronUp, SquarePen } from "lucide-react";
import { forwardRef, lazy, Suspense, useMemo, useState } from "react";
import type { FieldComponentProps } from "../../types/schema";
import type { ISchema } from "../../types/schema";

const LazyFormBuilder = lazy(() => import("../form-builder"));

const CHIP_MAX = 6;

/**
 * Coerce a control value into a valid {@link ISchema}. The STORED value is a JSON string holding a
 * bare array of `IField` (the workflow INPUT node's `input_schema` contract — the engine's
 * `_parse_input_schema` accepts only a list). We also tolerate a legacy `{ fields: [...] }` object
 * and an already-parsed value; anything empty / invalid / wrong-shape degrades to an empty schema.
 */
export function parseSchema(value: unknown): ISchema {
  const coerce = (v: unknown): ISchema | null => {
    if (Array.isArray(v)) return { fields: v as ISchema["fields"] };
    if (v && typeof v === "object" && Array.isArray((v as ISchema).fields)) return v as ISchema;
    return null;
  };
  const direct = coerce(value);
  if (direct) return direct;
  if (typeof value !== "string" || value.trim() === "") return { fields: [] };
  try {
    return coerce(JSON.parse(value)) ?? { fields: [] };
  } catch {
    return { fields: [] };
  }
}

export interface IFormBuilderControlProps extends FieldComponentProps {
  value?: string;
  onChange?: (value: string) => void;
}

const FormBuilderControl = forwardRef<HTMLDivElement, IFormBuilderControlProps>((props, ref) => {
  const { value, onChange } = props;
  const [expanded, setExpanded] = useState(false);
  const schema = useMemo(() => parseSchema(value), [value]);
  const fields = schema.fields;

  // Emit the BARE fields array — the INPUT engine's input_schema contract is a JSON list, not a
  // { fields } object (a dict is silently dropped by _parse_input_schema).
  const handleChange = (s: ISchema) => onChange?.(JSON.stringify(s.fields, null, 2));

  if (expanded) {
    return (
      <div ref={ref} className="rounded-lg border border-input overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
          <span className="text-xs font-medium text-muted-foreground">Form schema</span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 gap-1.5 text-xs"
            onClick={() => setExpanded(false)}
          >
            <ChevronUp className="size-3.5" /> Collapse
          </Button>
        </div>
        <div className="h-[480px] max-h-[480px] overflow-hidden">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                Loading builder…
              </div>
            }
          >
            <LazyFormBuilder value={schema} onChange={handleChange} />
          </Suspense>
        </div>
      </div>
    );
  }

  return (
    <div ref={ref} className="flex flex-col gap-2 rounded-lg border border-input p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">
          {fields.length} field{fields.length === 1 ? "" : "s"}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 gap-1.5 text-xs"
          onClick={() => setExpanded(true)}
        >
          <SquarePen className="size-3.5" /> Edit form
        </Button>
      </div>
      {fields.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {fields.slice(0, CHIP_MAX).map((f) => (
            <span
              key={f.key}
              className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
            >
              {f.key}
            </span>
          ))}
          {fields.length > CHIP_MAX && (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
              +{fields.length - CHIP_MAX}
            </span>
          )}
        </div>
      )}
    </div>
  );
});

FormBuilderControl.displayName = "FormBuilderControl";

export default FormBuilderControl;
