import { Input } from "@ldc/ui/components/input";
import { ToggleGroup, ToggleGroupItem } from "@ldc/ui/components/toggle-group";
import { buildGatewayOptionsServerConfig, parseGatewayOptionsServerConfig } from "../../../../../lib/gateway-options";
import { GatewayOptionsForm } from "./gateway-options-form";
import { OptionsEditor } from "./options-editor";
import { OptionsVariableInput } from "./options-variable-input";
import { ServerOptionsForm } from "./server-options-form";
import type { ISelectOption } from "../../../../field-control/select-control";
import { Section } from "../shared/panel-primitives";

export interface ChoiceConfigSectionProps {
    controlKey: string;
    controlProps: Record<string, unknown>;
    onControlPropsChange: (next: Record<string, unknown>) => void;
}

/** Combobox is the only Phase-A control with typeahead (SelectTable = Phase B). */
const TYPEAHEAD_CONTROLS = new Set(["ComboboxControl"]);

/** SelectTable (Phase B) is the only Gateway option-source that is paged (server
 *  pagination + total + contextValues); Select/RadioPopup/Combobox are not. */
const PAGED_CONTROLS = new Set(["SelectTableControl"]);

type DataSource = "static" | "server" | "gateway";

type StaticMode = "manual" | "variable";

/**
 * The data-source section for option-source Choice controls (Select / Combobox / Radio /
 * SelectTable). A schema carries EITHER `options` (static) OR `serverOptions` (legacy REST
 * or Gateway), never both — mirrors the runtime `serverOptions ? data : options`. Selecting
 * a source clears the others. Gateway `serverOptions` is detected structurally (POST to the
 * invoke_app_function endpoint) so opening an existing gateway-sourced field re-selects the
 * Gateway tab instead of the REST one.
 */
export function ChoiceConfigSection({ controlKey, controlProps, onControlPropsChange }: ChoiceConfigSectionProps) {
    const paged = PAGED_CONTROLS.has(controlKey);
    const isGateway = !!parseGatewayOptionsServerConfig(controlProps.serverOptions, { paged });
    const source: DataSource = !controlProps.serverOptions ? "static" : isGateway ? "gateway" : "server";

    const setSource = (next: DataSource) => {
        if (next === source) return;
        if (next === "static") {
            const { serverOptions: _drop, ...rest } = controlProps;
            onControlPropsChange(rest);
        } else if (next === "server") {
            const { options: _drop, ...rest } = controlProps;
            onControlPropsChange({ ...rest, serverOptions: isGateway ? {} : (controlProps.serverOptions ?? {}) });
        } else {
            const { options: _drop, ...rest } = controlProps;
            // A fresh Gateway tab must already be gateway-SHAPED (not `{}`) so the structural
            // detection above keeps recognising it as "gateway" on the very next render —
            // before an App/Function has even been picked.
            const empty = buildGatewayOptionsServerConfig({ appName: "", functionName: "", functionId: "", dataPath: "" }, { paged });
            onControlPropsChange({ ...rest, serverOptions: isGateway ? (controlProps.serverOptions ?? empty) : empty });
        }
    };

    const options = Array.isArray(controlProps.options) ? (controlProps.options as ISelectOption[]) : [];
    const staticMode: StaticMode = typeof controlProps.options === "string" ? "variable" : "manual";

    const setStaticMode = (next: StaticMode) => {
        if (next === staticMode) return;
        onControlPropsChange({ ...controlProps, options: next === "variable" ? "" : [] });
    };

    return (
        <div data-control={controlKey}>
            <Section title="Data source" />
            <div className="px-3 mb-2">
                <ToggleGroup
                    type="single"
                    aria-label="Data source"
                    value={source}
                    onValueChange={(v) => v && setSource(v as DataSource)}
                    variant="outline"
                    size="sm"
                    className="w-full grid grid-cols-3 bg-secondary rounded-md p-0.5"
                >
                    <ToggleGroupItem
                        value="static"
                        className="h-7 text-xs data-[state=on]:bg-card data-[state=on]:shadow-xs data-[state=on]:font-semibold"
                    >
                        Static list
                    </ToggleGroupItem>
                    <ToggleGroupItem
                        value="server"
                        className="h-7 text-xs data-[state=on]:bg-card data-[state=on]:shadow-xs data-[state=on]:font-semibold"
                    >
                        Server (REST)
                    </ToggleGroupItem>
                    <ToggleGroupItem
                        value="gateway"
                        className="h-7 text-xs data-[state=on]:bg-card data-[state=on]:shadow-xs data-[state=on]:font-semibold"
                    >
                        Gateway
                    </ToggleGroupItem>
                </ToggleGroup>
                <p className="text-[10px] text-muted-foreground mt-1">
                    Mutually exclusive with static options. Switching clears the other source.
                </p>
            </div>

            {/* valueKey/labelKey map an option object → {value,label}. The runtime reads them
                for BOTH static and server options (select/combobox/radio/select-table default
                to id/value), so they belong to the whole Choice group — not only server mode. */}
            <div className="grid grid-cols-2 gap-2 px-3 mb-2">
                <div className="flex flex-col gap-1">
                    <label className="text-[10px] font-medium text-muted-foreground">Value key</label>
                    <Input
                        aria-label="Value key"
                        className="h-8! text-xs!"
                        placeholder="id"
                        value={typeof controlProps.valueKey === "string" ? controlProps.valueKey : ""}
                        onChange={(e) => onControlPropsChange({ ...controlProps, valueKey: e.target.value })}
                    />
                </div>
                <div className="flex flex-col gap-1">
                    <label className="text-[10px] font-medium text-muted-foreground">Label key</label>
                    <Input
                        aria-label="Label key"
                        className="h-8! text-xs!"
                        placeholder="value"
                        value={typeof controlProps.labelKey === "string" ? controlProps.labelKey : ""}
                        onChange={(e) => onControlPropsChange({ ...controlProps, labelKey: e.target.value })}
                    />
                </div>
            </div>

            {source === "static" && (
                <>
                    <div className="px-3 mb-2">
                        <ToggleGroup
                            type="single"
                            aria-label="Options mode"
                            value={staticMode}
                            onValueChange={(v) => v && setStaticMode(v as StaticMode)}
                            variant="outline"
                            size="sm"
                            className="w-full grid grid-cols-2 bg-secondary rounded-md p-0.5"
                        >
                            <ToggleGroupItem
                                value="manual"
                                className="h-7 text-xs data-[state=on]:bg-card data-[state=on]:shadow-xs data-[state=on]:font-semibold"
                            >
                                Manual
                            </ToggleGroupItem>
                            <ToggleGroupItem
                                value="variable"
                                className="h-7 text-xs data-[state=on]:bg-card data-[state=on]:shadow-xs data-[state=on]:font-semibold"
                            >
                                Variable
                            </ToggleGroupItem>
                        </ToggleGroup>
                    </div>
                    {staticMode === "variable" ? (
                        <OptionsVariableInput
                            value={controlProps.options as string}
                            onChange={(next) => onControlPropsChange({ ...controlProps, options: next })}
                        />
                    ) : (
                        <OptionsEditor
                            options={options}
                            onChange={(next) => onControlPropsChange({ ...controlProps, options: next })}
                        />
                    )}
                </>
            )}

            {source === "server" && (
                <ServerOptionsForm
                    value={(controlProps.serverOptions as Record<string, unknown>) ?? {}}
                    onChange={(so) => onControlPropsChange({ ...controlProps, serverOptions: so })}
                />
            )}

            {source === "gateway" && (
                <GatewayOptionsForm
                    value={(controlProps.serverOptions as Record<string, unknown>) ?? {}}
                    onChange={(so) => onControlPropsChange({ ...controlProps, serverOptions: so })}
                    searchBinding={TYPEAHEAD_CONTROLS.has(controlKey)}
                    paged={paged}
                />
            )}
        </div>
    );
}

export default ChoiceConfigSection;
