/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { ChoiceConfigSection, type ChoiceConfigSectionProps } from "./choice-config-section";

rs.mock("../../../../field-control/code-control", () => ({
    __esModule: true,
    default: function MockCodeEditor({ value, onChange }: { value?: string; onChange?: (v: string) => void }) {
        return <textarea data-testid="code-editor" value={value ?? ""} onChange={(e) => onChange?.(e.target.value)} />;
    },
}));

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

/** Wires ChoiceConfigSection's onControlPropsChange back into React state — for
 *  assertions on UI that reacts to a PRIOR change (e.g. re-rendering under a
 *  newly-picked data source), which a one-shot mocked onChange can't show. */
function Harness({ controlKey, initialControlProps }: { controlKey: string; initialControlProps: Record<string, unknown> }) {
    const [controlProps, setControlProps] = useState<ChoiceConfigSectionProps["controlProps"]>(initialControlProps);
    return <ChoiceConfigSection controlKey={controlKey} controlProps={controlProps} onControlPropsChange={setControlProps} />;
}

describe("ChoiceConfigSection — Static branch", () => {
    it("defaults to Static and shows the OptionsEditor", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ options: [{ id: "a", value: "Apple" }] }}
                onControlPropsChange={rs.fn()}
            />
        );
        const group = screen.getByRole("radiogroup", { name: "Data source" });
        expect(group).toBeInTheDocument();
        expect(screen.getByRole("radio", { name: /static/i })).toBeChecked();
        expect(screen.getByRole("button", { name: /add option/i })).toBeInTheDocument();
    });

    it("exposes valueKey/labelKey in Static mode too (runtime maps option→value/label in both modes)", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ options: [{ id: "a", value: "Apple" }] }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.getByRole("radio", { name: /static/i })).toBeChecked();
        expect(screen.getByRole("textbox", { name: "Value key" })).toBeInTheDocument();
        expect(screen.getByRole("textbox", { name: "Label key" })).toBeInTheDocument();
    });

    it("edits static options through the OptionsEditor", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <ChoiceConfigSection controlKey="SelectControl" controlProps={{ options: [] }} onControlPropsChange={onChange} />
        );
        await user.click(screen.getByRole("button", { name: /add option/i }));
        expect(onChange).toHaveBeenCalled();
        expect(Array.isArray(onChange.mock.calls[0][0].options)).toBe(true);
    });

    it("switching to Server clears static options (options XOR serverOptions)", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ options: [{ id: "a", value: "Apple" }] }}
                onControlPropsChange={onChange}
            />
        );
        await user.click(screen.getByRole("radio", { name: /server/i }));
        const next = onChange.mock.calls[0][0];
        expect(next).not.toHaveProperty("options");
        expect(next).toHaveProperty("serverOptions");
    });
});

describe("ChoiceConfigSection — Server branch", () => {
    it("shows the serverOptions form + valueKey/labelKey when a server source is set", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ serverOptions: { endpoint: "odata/Country/data", service: "gateway" } }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.getByRole("radio", { name: /server/i })).toBeChecked();
        expect(screen.getByRole("textbox", { name: "Endpoint" })).toHaveValue("odata/Country/data");
        expect(screen.getByRole("textbox", { name: "Value key" })).toBeInTheDocument();
        expect(screen.getByRole("textbox", { name: "Label key" })).toBeInTheDocument();
        // Static editor is gone on the Server branch.
        expect(screen.queryByRole("button", { name: /add option/i })).not.toBeInTheDocument();
    });

    it("writes serverOptions edits back under serverOptions", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ serverOptions: { endpoint: "" } }}
                onControlPropsChange={onChange}
            />
        );
        await user.type(screen.getByRole("textbox", { name: "Value key" }), "x");
        const next = onChange.mock.calls[0][0];
        expect(next.valueKey).toBe("x");
        expect(next).toHaveProperty("serverOptions");
    });

    it("switching back to Static clears serverOptions", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ serverOptions: { endpoint: "x", service: "gateway" } }}
                onControlPropsChange={onChange}
            />
        );
        await user.click(screen.getByRole("radio", { name: /static/i }));
        expect(onChange.mock.calls[0][0]).not.toHaveProperty("serverOptions");
    });
});

describe("ChoiceConfigSection — Gateway branch (SA-2184)", () => {
    const GATEWAY_SERVER_OPTIONS = {
        service: "workflow",
        endpoint: "workers/gateway/functions/invoke_app_function/invoke",
        method: "POST",
        body: { params: { app: "file-service", function: "country_list", args: {} } },
        responseMapping: { dataPath: "data.result.body" },
    };

    it("re-selects the Gateway tab (not REST) when an existing gateway serverOptions is opened", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ serverOptions: GATEWAY_SERVER_OPTIONS }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.getByRole("radio", { name: /^gateway$/i })).toBeChecked();
        expect(screen.getByRole("combobox", { name: "Gateway app" })).toBeInTheDocument();
    });

    it("a legacy REST serverOptions (no method) still opens under Server (REST), not Gateway", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ serverOptions: { endpoint: "odata/Country/data", service: "gateway" } }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.getByRole("radio", { name: /server/i })).toBeChecked();
        expect(screen.queryByRole("combobox", { name: "Gateway app" })).not.toBeInTheDocument();
    });

    it("switching to Gateway clears static options and mode/odataConfig never render", async () => {
        const user = userEvent.setup();
        render(<Harness controlKey="SelectControl" initialControlProps={{ options: [{ id: "a", value: "Apple" }] }} />);
        await user.click(screen.getByRole("radio", { name: /^gateway$/i }));

        // Mode/odataConfig fields (from the legacy ServerOptionsForm) must be absent on Gateway.
        expect(screen.queryByRole("textbox", { name: "Search field" })).not.toBeInTheDocument();
        expect(screen.getByRole("combobox", { name: "Gateway app" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /add option/i })).not.toBeInTheDocument();
    });

    it("shows the Search binding section only for Combobox (typeahead), not Select", () => {
        render(
            <ChoiceConfigSection
                controlKey="ComboboxControl"
                controlProps={{ serverOptions: GATEWAY_SERVER_OPTIONS }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.getByRole("combobox", { name: "Search param" })).toBeInTheDocument();
    });

    it("Select (no typeahead) does not render a Search binding section", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ serverOptions: GATEWAY_SERVER_OPTIONS }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.queryByRole("combobox", { name: "Search param" })).not.toBeInTheDocument();
    });
});

describe("ChoiceConfigSection — SelectTable is paged (Phase B, SA-2192)", () => {
    const GATEWAY_SERVER_OPTIONS = {
        service: "workflow",
        endpoint: "workers/gateway/functions/invoke_app_function/invoke",
        method: "POST",
        body: { params: { app: "file-service", function: "city_list", args: {} } },
        responseMapping: { dataPath: "data.result.body.data" },
    };

    it("re-selects the Gateway tab for an existing paged SelectTable serverOptions", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectTableControl"
                controlProps={{ serverOptions: GATEWAY_SERVER_OPTIONS }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.getByRole("radio", { name: /^gateway$/i })).toBeChecked();
    });

    it("does not render the Search binding section (SelectTable search rides $search directly, not searchArgKey)", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectTableControl"
                controlProps={{ serverOptions: GATEWAY_SERVER_OPTIONS }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.queryByRole("combobox", { name: "Search param" })).not.toBeInTheDocument();
    });

    it("shows Total path + Context values (paged-only authoring) under Advanced", async () => {
        const user = userEvent.setup();
        render(
            <ChoiceConfigSection
                controlKey="SelectTableControl"
                controlProps={{ serverOptions: GATEWAY_SERVER_OPTIONS }}
                onControlPropsChange={rs.fn()}
            />
        );
        await user.click(screen.getByRole("button", { name: /advanced — response mapping/i }));
        expect(screen.getByRole("textbox", { name: "Total path" })).toBeInTheDocument();
        expect(screen.getByText("Context values")).toBeInTheDocument();
    });

    it("Select (not paged) has no Total path / Context values authoring", async () => {
        const user = userEvent.setup();
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{
                    serverOptions: { ...GATEWAY_SERVER_OPTIONS, responseMapping: { dataPath: "data.result.body" } },
                }}
                onControlPropsChange={rs.fn()}
            />
        );
        await user.click(screen.getByRole("button", { name: /advanced — response mapping/i }));
        expect(screen.queryByRole("textbox", { name: "Total path" })).not.toBeInTheDocument();
        expect(screen.queryByText("Context values")).not.toBeInTheDocument();
    });

    it("a fresh Gateway tab for SelectTable is already paged-shaped (Data path defaults to .data)", async () => {
        const user = userEvent.setup();
        render(<Harness controlKey="SelectTableControl" initialControlProps={{ options: [] }} />);
        await user.click(screen.getByRole("radio", { name: /^gateway$/i }));
        await user.click(screen.getByRole("button", { name: /advanced — response mapping/i }));
        expect(screen.getByRole("textbox", { name: "Data path" })).toHaveAttribute("placeholder", "data.result.body.data");
    });
});

describe("ChoiceConfigSection — Static options from a variable", () => {
    it("defaults to Manual when options is an array", () => {
        render(
            <ChoiceConfigSection controlKey="SelectControl" controlProps={{ options: [] }} onControlPropsChange={rs.fn()} />
        );
        expect(screen.getByRole("radio", { name: /^manual$/i })).toBeChecked();
        expect(screen.getByRole("button", { name: /add option/i })).toBeInTheDocument();
    });

    it("opens in Variable mode when options is a string", () => {
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ options: "{{$http_1.response_body.items}}" }}
                onControlPropsChange={rs.fn()}
            />
        );
        expect(screen.getByRole("radio", { name: /^variable$/i })).toBeChecked();
        expect(screen.getByRole("textbox", { name: "Options variable" })).toHaveValue("{{$http_1.response_body.items}}");
        expect(screen.queryByRole("button", { name: /add option/i })).not.toBeInTheDocument();
    });

    it("Manual → Variable writes an empty string and keeps other props", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <ChoiceConfigSection
                controlKey="SelectControl"
                controlProps={{ options: [{ id: "a", value: "Apple" }], valueKey: "code" }}
                onControlPropsChange={onChange}
            />
        );
        await user.click(screen.getByRole("radio", { name: /^variable$/i }));
        expect(onChange).toHaveBeenCalledWith({ options: "", valueKey: "code" });
    });

    it("Variable → Manual writes an empty list", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <ChoiceConfigSection controlKey="SelectControl" controlProps={{ options: "{{$n.items}}" }} onControlPropsChange={onChange} />
        );
        await user.click(screen.getByRole("radio", { name: /^manual$/i }));
        expect(onChange).toHaveBeenCalledWith({ options: [] });
    });

    it("keeps Variable mode after switching and editing", async () => {
        const user = userEvent.setup();
        render(<Harness controlKey="SelectControl" initialControlProps={{ options: [] }} />);
        await user.click(screen.getByRole("radio", { name: /^variable$/i }));
        expect(screen.getByRole("textbox", { name: "Options variable" })).toHaveValue("");
    });
});
