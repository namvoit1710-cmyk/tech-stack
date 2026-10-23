import { Button } from "@ldc/ui/components/button";
import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { Switch } from "@ldc/ui/components/switch";
import { Plus, Trash2 } from "lucide-react";
import {
    IGatewayFunctionParam,
    IGatewayOutputField,
    PARAM_LOCATIONS,
    PARAM_TYPES,
    ParamLocation,
    ParamType,
} from "../../types/gateway";

const TypeSelect = ({ value, onChange }: { value: ParamType; onChange: (v: ParamType) => void }) => (
    <Select value={value} onValueChange={(v) => onChange(v as ParamType)}>
        <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
        <SelectContent>
            {PARAM_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
        </SelectContent>
    </Select>
);

/** Repeatable rows for a function's input_params ({key,type,location,required,label}). */
export const InputParamsBuilder = ({
    value,
    onChange,
}: {
    value: IGatewayFunctionParam[];
    onChange: (rows: IGatewayFunctionParam[]) => void;
}) => {
    const update = (i: number, patch: Partial<IGatewayFunctionParam>) =>
        onChange(value.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
    const remove = (i: number) => onChange(value.filter((_, idx) => idx !== i));
    const add = () =>
        onChange([...value, { key: "", type: "string", location: "query", required: false, label: "" }]);

    return (
        <div className="flex flex-col gap-2">
            {value.length === 0 && <p className="text-sm text-muted-foreground">No input parameters.</p>}
            {value.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                    <Input className="flex-1" placeholder="key" value={row.key} onChange={(e) => update(i, { key: e.target.value })} />
                    <TypeSelect value={row.type} onChange={(type) => update(i, { type })} />
                    <Select value={row.location} onValueChange={(v) => update(i, { location: v as ParamLocation })}>
                        <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            {PARAM_LOCATIONS.map((l) => <SelectItem key={l} value={l}>{l}</SelectItem>)}
                        </SelectContent>
                    </Select>
                    <Input className="flex-1" placeholder="label" value={row.label} onChange={(e) => update(i, { label: e.target.value })} />
                    <label className="flex items-center gap-1 text-xs text-muted-foreground shrink-0">
                        <Switch checked={row.required} onCheckedChange={(c) => update(i, { required: c })} /> req
                    </label>
                    <Button type="button" variant="ghost" size="icon" onClick={() => remove(i)}>
                        <Trash2 className="size-4" />
                    </Button>
                </div>
            ))}
            <Button type="button" variant="outline" size="sm" className="self-start" onClick={add}>
                <Plus className="size-4 mr-1" /> Add parameter
            </Button>
        </div>
    );
};

/** Repeatable rows for a function's output_fields ({key,type,label}). */
export const OutputFieldsBuilder = ({
    value,
    onChange,
}: {
    value: IGatewayOutputField[];
    onChange: (rows: IGatewayOutputField[]) => void;
}) => {
    const update = (i: number, patch: Partial<IGatewayOutputField>) =>
        onChange(value.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
    const remove = (i: number) => onChange(value.filter((_, idx) => idx !== i));
    const add = () => onChange([...value, { key: "", type: "string", label: "" }]);

    return (
        <div className="flex flex-col gap-2">
            {value.length === 0 && <p className="text-sm text-muted-foreground">No output fields.</p>}
            {value.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                    <Input className="flex-1" placeholder="key" value={row.key} onChange={(e) => update(i, { key: e.target.value })} />
                    <TypeSelect value={row.type} onChange={(type) => update(i, { type })} />
                    <Input className="flex-1" placeholder="label" value={row.label} onChange={(e) => update(i, { label: e.target.value })} />
                    <Button type="button" variant="ghost" size="icon" onClick={() => remove(i)}>
                        <Trash2 className="size-4" />
                    </Button>
                </div>
            ))}
            <Button type="button" variant="outline" size="sm" className="self-start" onClick={add}>
                <Plus className="size-4 mr-1" /> Add output field
            </Button>
        </div>
    );
};
