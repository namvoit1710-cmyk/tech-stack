import { useState } from "react";

export interface ContainerDefaultFieldProps {
  /** Current container default (object for Tabs, array for OData). `undefined` = none. */
  value: unknown;
  /** Called with the parsed value, or `undefined` when the field is cleared. */
  onChange: (next: unknown) => void;
  placeholder?: string;
}

/**
 * A JSON editor for a CONTAINER's default value. Containers hold structured defaults
 * (a Tabs object, an OData rows array), so a typed single-line widget does not fit — the
 * user edits raw JSON and it is parsed on every keystroke. Invalid JSON is shown as an
 * error and NOT written, so the node never holds an unparseable default.
 *
 * Seed-once from `value` via `useState`; the caller passes `key={node._id}` so switching
 * nodes remounts and reseeds. That keeps the textarea from reformatting under the user's
 * cursor when their own edit flows back through `value`.
 */
export function ContainerDefaultField({ value, onChange, placeholder }: ContainerDefaultFieldProps) {
  const [text, setText] = useState(() => (value === undefined ? "" : JSON.stringify(value, null, 2)));
  const [error, setError] = useState<string | null>(null);

  const handleChange = (raw: string) => {
    setText(raw);
    if (raw.trim() === "") {
      setError(null);
      onChange(undefined);
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      setError(null);
      onChange(parsed);
    } catch {
      setError("Invalid JSON — not saved");
    }
  };

  return (
    <div className="flex flex-col gap-1 px-3 mb-2">
      <label className="text-[10px] font-medium text-muted-foreground">Default value (JSON)</label>
      <textarea
        aria-label="Default value (JSON)"
        className="min-h-16 rounded-md border border-border bg-background p-2 text-xs font-mono resize-y"
        placeholder={placeholder ?? "{ }"}
        value={text}
        onChange={(e) => handleChange(e.target.value)}
      />
      {error ? (
        <p className="text-[10px] text-destructive">{error}</p>
      ) : (
        <p className="text-[10px] text-muted-foreground/80">Seeds the form's initial value for this container.</p>
      )}
    </div>
  );
}

export default ContainerDefaultField;
