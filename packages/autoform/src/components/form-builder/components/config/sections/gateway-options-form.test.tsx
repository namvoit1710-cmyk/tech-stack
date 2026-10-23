/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React, { useState } from "react";
import { BuilderProvider } from "../../../../../contexts/builder.context";
import { GatewayOptionsForm } from "./gateway-options-form";

/** Wires GatewayOptionsForm's onChange back into React state — mirrors how
 *  ChoiceConfigSection actually drives it, so assertions on UI that reacts to
 *  a PRIOR change (a revealed constant input, a re-selected value) are real. */
function Harness({
    initialValue,
    searchBinding = false,
    paged = false,
    services,
}: {
    initialValue: Record<string, unknown>;
    searchBinding?: boolean;
    paged?: boolean;
    services?: Record<string, unknown>;
}) {
    const [value, setValue] = useState(initialValue);
    return (
        <BuilderProvider
            value={{ schema: { fields: [] }, fieldComponent: {}, wrapperComponent: {}, services: services as any }}
        >
            <GatewayOptionsForm value={value} onChange={setValue} searchBinding={searchBinding} paged={paged} />
        </BuilderProvider>
    );
}

afterEach(cleanup);
/**
 * The pickers are `@ldc/ui` Selects (Radix), not native `<select>`: the trigger is a
 * button and the options live in a portal, so `selectOptions` does not apply. Same
 * click-trigger-then-click-option idiom the other config-panel tests use.
 */
async function pick(user: ReturnType<typeof userEvent.setup>, name: string, option: string) {
    await user.click(screen.getByRole("combobox", { name }));
    await user.click(screen.getByRole("option", { name: option }));
}

/** Radix only renders its items while open — open the picker, then assert. */
async function expectOptionLoaded(user: ReturnType<typeof userEvent.setup>, name: string, option: string) {
    await waitFor(async () => {
        await user.click(screen.getByRole("combobox", { name }));
        expect(screen.getByRole("option", { name: option })).toBeInTheDocument();
    });
    await user.keyboard("{Escape}");
}

/** Radix renders the chosen label as the trigger's text, not as a form value. */
const expectPicked = (name: string, label: string) =>
    expect(screen.getByRole("combobox", { name })).toHaveTextContent(label);


const APPS = [{ name: "file-service" }, { name: "sap-erp" }];
const FUNCTIONS = [{
    name: "country_list",
    input_params: [
        { key: "$search", label: "Search", location: "query" },
        { key: "region_id", label: "Region", location: "query" },
        { key: "orderby", label: "Order by", location: "query" },
    ],
}];

/**
 * The pickers are gateway option sources themselves: they POST the same
 * invoke_app_function body every authored control uses, against the
 * `control-plane` app. The registry rows therefore arrive DOUBLE-wrapped —
 * the invoke envelope around the control plane's own `{status,data:{items}}`.
 */
const invokeEnvelope = (items: unknown[]) => ({ data: { result: { body: { data: { items } } } } });

function makePostMock() {
    return rs.fn((_endpoint: string, body?: any) => {
        const params = body?.params ?? {};
        if (params.function === "list_gateway_apps") return Promise.resolve(invokeEnvelope(APPS));
        if (params.function === "list_gateway_functions") {
            // the picked app rides args.app — no app, no rows (unmet dependency)
            return Promise.resolve(invokeEnvelope(params.args?.app ? FUNCTIONS : []));
        }
        return Promise.resolve(invokeEnvelope([]));
    });
}

function renderWithServices(props: Partial<React.ComponentProps<typeof GatewayOptionsForm>> = {}) {
    const onChange = rs.fn();
    const postMock = makePostMock();
    render(
        <BuilderProvider
            value={{
                schema: { fields: [] },
                fieldComponent: {},
                wrapperComponent: {},
                services: { workflow: { post: postMock } } as any,
            }}
        >
            <GatewayOptionsForm value={{}} onChange={onChange} searchBinding={false} {...props} />
        </BuilderProvider>
    );
    return { onChange, postMock };
}

describe("GatewayOptionsForm — no host services wired", () => {
    it("renders empty App/Function pickers without throwing", () => {
        render(<GatewayOptionsForm value={{}} onChange={rs.fn()} searchBinding={false} />);
        expect(screen.getByRole("combobox", { name: "Gateway app" })).toBeInTheDocument();
        expect(screen.getByRole("combobox", { name: "Gateway function" })).toBeDisabled();
    });
});

describe("GatewayOptionsForm — App/Function pickers (SA-2184)", () => {
    it("populates the App picker by invoking the control-plane gateway function", async () => {
        const { postMock } = renderWithServices();
        await waitFor(() => expect(postMock).toHaveBeenCalled());
        expect(postMock).toHaveBeenCalledWith(
            "workers/gateway/functions/invoke_app_function/invoke",
            { params: { app: "control-plane", function: "list_gateway_apps", args: {} } }
        );
        const user = userEvent.setup();
        await expectOptionLoaded(user, "Gateway app", "file-service");
        await user.click(screen.getByRole("combobox", { name: "Gateway app" }));
        expect(screen.getByRole("option", { name: "sap-erp" })).toBeInTheDocument();
    });

    it("the Function picker declares the App picker as its dependency (app rides args.app)", async () => {
        const { postMock } = renderWithServices({
            value: {
                service: "workflow",
                endpoint: "workers/gateway/functions/invoke_app_function/invoke",
                method: "POST",
                body: { params: { app: "file-service", function: "", args: {} } },
                responseMapping: { dataPath: "data.result.body" },
            },
        });
        await waitFor(() =>
            expect(postMock).toHaveBeenCalledWith(
                "workers/gateway/functions/invoke_app_function/invoke",
                { params: { app: "control-plane", function: "list_gateway_functions", args: { app: "file-service" } } }
            )
        );
    });

    it("does not invoke the function list until an App is picked", async () => {
        const { postMock } = renderWithServices();
        await waitFor(() => expect(postMock).toHaveBeenCalled());
        const listedFunctions = postMock.mock.calls.some(
            ([, body]: [string, any]) => body?.params?.function === "list_gateway_functions"
        );
        expect(listedFunctions).toBe(false);
    });

    it("Function picker is disabled until an App is picked, then fetches that app's functions", async () => {
        const { onChange } = renderWithServices();
        expect(screen.getByRole("combobox", { name: "Gateway function" })).toBeDisabled();

        const user = userEvent.setup();
        await expectOptionLoaded(user, "Gateway app", "file-service");
        await pick(user, "Gateway app", "file-service");

        const next = onChange.mock.calls.at(-1)![0];
        expect(next.body.params.app).toBe("file-service");
        expect(next.body.params.function).toBe("");
    });

    it("picking App then Function emits a gateway serverOptions with no hand-typed JSON", async () => {
        const onChange = rs.fn();
        const postMock = makePostMock();
        render(
            <BuilderProvider value={{ schema: { fields: [] }, fieldComponent: {}, wrapperComponent: {}, services: { workflow: { post: postMock } } as any }}>
                <GatewayOptionsForm value={{ body: { params: { app: "file-service", function: "", args: {} } }, method: "POST", service: "workflow", endpoint: "workers/gateway/functions/invoke_app_function/invoke", responseMapping: { dataPath: "data.result.body" } }} onChange={onChange} searchBinding={false} />
            </BuilderProvider>
        );
        const user = userEvent.setup();
        await expectOptionLoaded(user, "Gateway function", "country_list");
        await pick(user, "Gateway function", "country_list");

        const next = onChange.mock.calls.at(-1)![0];
        expect(next).toEqual({
            service: "workflow",
            endpoint: "workers/gateway/functions/invoke_app_function/invoke",
            method: "POST",
            body: { params: { app: "file-service", function: "country_list", args: {} } },
            responseMapping: { dataPath: "data.result.body" },
        });
    });
});

describe("GatewayOptionsForm — parameter bindings (SA-2184)", () => {
    const VALUE_WITH_FUNCTION = {
        service: "workflow",
        endpoint: "workers/gateway/functions/invoke_app_function/invoke",
        method: "POST",
        body: { params: { app: "file-service", function: "country_list", args: {} } },
        responseMapping: { dataPath: "data.result.body" },
    };

    it("lists every input_param with an expose/lock selector, defaulting to expose", async () => {
        renderWithServices({ value: VALUE_WITH_FUNCTION });
        await waitFor(() => expect(screen.getByRole("combobox", { name: "region_id binding mode" })).toBeInTheDocument());
        expectPicked("region_id binding mode", "expose");
        expect(screen.getByText(/dep field: region_id/)).toBeInTheDocument();
    });

    it("switching a param to lock reveals a constant input and bakes it into body.params.args", async () => {
        render(<Harness initialValue={VALUE_WITH_FUNCTION} services={{ workflow: { post: makePostMock() } }} />);
        await waitFor(() => expect(screen.getByRole("combobox", { name: "orderby binding mode" })).toBeInTheDocument());

        const user = userEvent.setup();
        await pick(user, "orderby binding mode", "lock");
        await waitFor(() => expect(screen.getByRole("textbox", { name: "orderby constant" })).toBeInTheDocument());
        fireEvent.change(screen.getByRole("textbox", { name: "orderby constant" }), { target: { value: "name asc" } });

        await waitFor(() => expect(screen.getByRole("textbox", { name: "orderby constant" })).toHaveValue("name asc"));
    });

    it("an exposed param becomes a dependency (cascading refetch)", async () => {
        // Start from region_id already locked, so switching it to expose is a real,
        // single value transition — not dependent on the isolated component re-rendering
        // with its own emitted value (that wiring lives in ChoiceConfigSection).
        const VALUE_WITH_LOCKED_REGION = {
            ...VALUE_WITH_FUNCTION,
            body: { params: { app: "file-service", function: "country_list", args: { region_id: "eu" } } },
        };
        const { onChange } = renderWithServices({ value: VALUE_WITH_LOCKED_REGION });
        await waitFor(() => expectPicked("region_id binding mode", "lock"));

        const user = userEvent.setup();
        await pick(user, "region_id binding mode", "expose");

        const last = onChange.mock.calls.at(-1)![0];
        expect(last.dependencies).toContain("region_id");
    });
});

describe("GatewayOptionsForm — search binding (Combobox typeahead only)", () => {
    const VALUE_WITH_FUNCTION = {
        service: "workflow",
        endpoint: "workers/gateway/functions/invoke_app_function/invoke",
        method: "POST",
        body: { params: { app: "file-service", function: "country_list", args: {} } },
        responseMapping: { dataPath: "data.result.body" },
    };

    it("defaults the search param to $search and emits searchArgKey when changed", async () => {
        const { onChange } = renderWithServices({ value: VALUE_WITH_FUNCTION, searchBinding: true });
        await waitFor(() => expect(screen.getByRole("combobox", { name: "Search param" })).toBeInTheDocument());
        expectPicked("Search param", "$search");

        const user = userEvent.setup();
        await pick(user, "Search param", "region_id");
        const last = onChange.mock.calls.at(-1)![0];
        expect(last.searchArgKey).toBe("region_id");
    });

    it("omits the Search binding section when searchBinding=false (Select/RadioPopup)", () => {
        renderWithServices({ value: VALUE_WITH_FUNCTION, searchBinding: false });
        expect(screen.queryByRole("combobox", { name: "Search param" })).not.toBeInTheDocument();
    });
});

describe("GatewayOptionsForm — response mapping (Advanced, Phase A default)", () => {
    it("Data path defaults to data.result.body and is editable", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<GatewayOptionsForm value={{}} onChange={onChange} searchBinding={false} />);

        await user.click(screen.getByRole("button", { name: /advanced — response mapping/i }));
        const dataPathInput = screen.getByRole("textbox", { name: "Data path" });
        expect(dataPathInput).toHaveAttribute("placeholder", "data.result.body");

        fireEvent.change(dataPathInput, { target: { value: "data.result.body.rows" } });
        const last = onChange.mock.calls.at(-1)![0];
        expect(last.responseMapping.dataPath).toBe("data.result.body.rows");
    });

    it("omits Total path + Context values when paged=false (Select/RadioPopup/Combobox)", async () => {
        const user = userEvent.setup();
        render(<GatewayOptionsForm value={{}} onChange={rs.fn()} searchBinding={false} paged={false} />);
        await user.click(screen.getByRole("button", { name: /advanced — response mapping/i }));
        expect(screen.queryByRole("textbox", { name: "Total path" })).not.toBeInTheDocument();
        expect(screen.queryByText("Context values")).not.toBeInTheDocument();
    });
});

describe("GatewayOptionsForm — paged response mapping (SelectTable, SA-2192)", () => {
    it("Data path defaults one level deeper (data.result.body.data) when paged", async () => {
        const user = userEvent.setup();
        render(<GatewayOptionsForm value={{}} onChange={rs.fn()} searchBinding={false} paged={true} />);
        await user.click(screen.getByRole("button", { name: /advanced — response mapping/i }));
        expect(screen.getByRole("textbox", { name: "Data path" })).toHaveAttribute("placeholder", "data.result.body.data");
    });

    it("Total path defaults to data.result.body.total_count and is editable", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<GatewayOptionsForm value={{}} onChange={onChange} searchBinding={false} paged={true} />);
        await user.click(screen.getByRole("button", { name: /advanced — response mapping/i }));

        const totalPathInput = screen.getByRole("textbox", { name: "Total path" });
        expect(totalPathInput).toHaveAttribute("placeholder", "data.result.body.total_count");

        fireEvent.change(totalPathInput, { target: { value: "data.result.body.count" } });
        const last = onChange.mock.calls.at(-1)![0];
        expect(last.responseMapping.totalPath).toBe("data.result.body.count");
    });

    it("adds a context value row and emits it as one queryParams.contextValues arg", async () => {
        const user = userEvent.setup();
        render(<Harness initialValue={{}} paged={true} />);

        await user.click(screen.getByRole("button", { name: "+ Add context value" }));
        expect(screen.getByRole("textbox", { name: "Context value 0 API key" })).toHaveValue("");

        fireEvent.change(screen.getByRole("textbox", { name: "Context value 0 API key" }), { target: { value: "tenant_id" } });
        await waitFor(() => expect(screen.getByRole("textbox", { name: "Context value 0 API key" })).toHaveValue("tenant_id"));
        fireEvent.change(screen.getByRole("textbox", { name: "Context value 0 form field" }), { target: { value: "tenant" } });
        await waitFor(() => expect(screen.getByRole("textbox", { name: "Context value 0 form field" })).toHaveValue("tenant"));
    });

    it("removes a context value row", async () => {
        const user = userEvent.setup();
        render(
            <Harness
                paged={true}
                initialValue={{
                    method: "POST",
                    endpoint: "workers/gateway/functions/invoke_app_function/invoke",
                    service: "workflow",
                    body: { params: { app: "a", function: "f", args: {} } },
                    responseMapping: { dataPath: "data.result.body.data" },
                    queryParams: { contextValues: encodeURIComponent(JSON.stringify({ tenant_id: "tenant" })) },
                }}
            />
        );
        expect(screen.getByRole("textbox", { name: "Context value 0 API key" })).toHaveValue("tenant_id");

        await user.click(screen.getByRole("button", { name: "Remove context value 0" }));
        expect(screen.queryByRole("textbox", { name: "Context value 0 API key" })).not.toBeInTheDocument();
    });
});
