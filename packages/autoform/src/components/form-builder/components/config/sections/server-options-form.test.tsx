/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SERVER_OPTIONS_SCHEMA, ServerOptionsForm } from "./server-options-form";

rs.mock("../../../../field-control/code-control", () => ({
    __esModule: true,
    default: function MockCodeEditor({ value, onChange }: { value?: string; onChange?: (v: string) => void }) {
        return <textarea data-testid="code-editor" value={value ?? ""} onChange={(e) => onChange?.(e.target.value)} />;
    },
}));

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

describe("SERVER_OPTIONS_SCHEMA", () => {
    it("mirrors IServerOptionsConfig field-for-field", () => {
        const keys = SERVER_OPTIONS_SCHEMA.map((p) => p.key);
        expect(keys).toEqual([
            "endpoint", "service", "mode", "method", "dependencies",
            "responseMapping", "odataConfig", "params", "queryParams", "body",
        ]);
        const byKey = Object.fromEntries(SERVER_OPTIONS_SCHEMA.map((p) => [p.key, p]));
        expect(byKey.mode).toMatchObject({ type: "enum", options: ["default", "odata"] });
        expect(byKey.method).toMatchObject({ type: "enum", options: ["GET", "POST"] });
        expect(byKey.dependencies?.type).toBe("list");
        expect(byKey.responseMapping?.type).toBe("object");
        expect(byKey.odataConfig?.type).toBe("object");
        expect(byKey.params?.type).toBe("keyValue");
        expect(byKey.queryParams?.type).toBe("keyValue");
        expect(byKey.body?.type).toBe("keyValue");
    });
});

describe("ServerOptionsForm", () => {
    it("renders each field with its description and the required endpoint/service", () => {
        render(<ServerOptionsForm value={{ endpoint: "odata/Country/data", service: "gateway" }} onChange={rs.fn()} />);
        expect(screen.getByRole("textbox", { name: "Endpoint" })).toHaveValue("odata/Country/data");
        expect(screen.getByRole("textbox", { name: "Service" })).toHaveValue("gateway");
        expect(screen.getByText(/Service\/gateway registered in @ldc\/api-sdk/i)).toBeInTheDocument();
        // nested object groups
        expect(screen.getByRole("textbox", { name: "Data path" })).toBeInTheDocument();
        expect(screen.getByRole("textbox", { name: "Search field" })).toBeInTheDocument();
    });

    it("adds a query param key-value row", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<ServerOptionsForm value={{ queryParams: {} }} onChange={onChange} />);
        await user.click(screen.getByRole("button", { name: /add query params/i }));
        const next = onChange.mock.calls[0][0];
        expect(next.queryParams).toEqual({ "": "" });
    });

    it("edits a dependency (list) entry", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<ServerOptionsForm value={{ dependencies: ["a"] }} onChange={onChange} />);
        await user.type(screen.getByRole("textbox", { name: "Dependencies 1" }), "b");
        expect(onChange).toHaveBeenCalledWith({ dependencies: ["ab"] });
    });
});
