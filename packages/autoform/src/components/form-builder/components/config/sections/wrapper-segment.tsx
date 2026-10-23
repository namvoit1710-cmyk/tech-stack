import { ToggleGroup, ToggleGroupItem } from "@ldc/ui/components/toggle-group";

const WRAPPERS = ["FormItemWrapper", "DivWrapper", "FragmentWrapper"];
const labelOf = (w: string) => (w === "FormItemWrapper" ? "Form Item" : w.replace("Wrapper", ""));

/** Segmented 3-way wrapper selector (replaces the old <Select>). One option is always active. */
export function WrapperSegment({ value, onChange }: { value: string; onChange: (w: string) => void }) {
    return (
        <div className="flex flex-col gap-1.5 w-full px-3 mb-2">
            <label className="text-[10px] font-medium text-muted-foreground">Wrapper</label>
            <ToggleGroup
                type="single"
                value={value}
                onValueChange={(v) => {
                    if (!v) return;
                    onChange(v);
                }}
                variant="outline"
                size="sm"
                className="w-full grid grid-cols-3 bg-secondary rounded-md p-0.5"
            >
                {WRAPPERS.map((w) => (
                    <ToggleGroupItem
                        key={w}
                        value={w}
                        className="h-7 text-xs data-[state=on]:bg-card data-[state=on]:shadow-xs data-[state=on]:font-semibold"
                    >
                        {labelOf(w)}
                    </ToggleGroupItem>
                ))}
            </ToggleGroup>
        </div>
    );
}
