import type { PropSchema } from "../../../constants/prop-keys";
import { PropSchemaFields } from "./prop-schema-fields";

/**
 * The `serverOptions` structured form — the schema tree mirrors `IServerOptionsConfig`
 * (types/server-option-config.ts) field-for-field. The user only fills values; nothing
 * here is raw JSON. `responseMapping`/`odataConfig` are nested `object` groups;
 * `params`/`queryParams`/`body` are `keyValue` maps; `dependencies` is a `list`.
 */
export const SERVER_OPTIONS_SCHEMA: PropSchema[] = [
    { key: "endpoint", type: "string", label: "Endpoint", help: "API path returning options, e.g. odata/Country/data" },
    { key: "service", type: "string", label: "Service", help: "Service/gateway registered in @ldc/api-sdk" },
    { key: "mode", type: "enum", label: "Mode", options: ["default", "odata"], help: "default = plain REST · odata = builds $filter/$orderby/$select" },
    { key: "method", type: "enum", label: "Method", options: ["GET", "POST"], help: "POST carries body" },
    { key: "dependencies", type: "list", label: "Dependencies", help: "Field keys this source depends on; change → refetch" },
    {
        key: "responseMapping",
        type: "object",
        label: "Response mapping",
        fields: [
            { key: "dataPath", type: "string", label: "Data path", help: "JSON path to the data array (default data.items)" },
            { key: "totalPath", type: "string", label: "Total path", help: "JSON path to the total (default data.count)" },
        ],
    },
    {
        key: "odataConfig",
        type: "object",
        label: "OData config",
        fields: [
            { key: "defaultFilter", type: "string", label: "Default filter" },
            { key: "defaultOrderBy", type: "string", label: "Default orderBy" },
            { key: "defaultSelect", type: "string", label: "Default select" },
            { key: "searchField", type: "string", label: "Search field" },
        ],
    },
    { key: "params", type: "keyValue", label: "Params", help: "Static params merged into every request" },
    { key: "queryParams", type: "keyValue", label: "Query params", help: "Query params (may hold contextValues)" },
    { key: "body", type: "keyValue", label: "Body", help: "POST body" },
];

export interface ServerOptionsFormProps {
    value: Record<string, unknown>;
    onChange: (next: Record<string, unknown>) => void;
}

export function ServerOptionsForm({ value, onChange }: ServerOptionsFormProps) {
    return <PropSchemaFields schema={SERVER_OPTIONS_SCHEMA} value={value} onChange={onChange} />;
}

export default ServerOptionsForm;
