import { Fragment, type ReactNode } from "react";
import { env } from "@/env";

// A node output/event payload often references a stored file artifact
// (__file_ref → file_id). Turn each file_id into a link that opens the
// file-service OData JSON in a new tab. Uses the existing PUBLIC_URL_FILE_SERVICE
// (tenantized at deploy — no new env); on deploy the tenant's own file-service
// answers, locally point it at a real one.
export const fileOdataUrl = (fileId: string) => `${env.PUBLIC_URL_FILE_SERVICE}/api/v1/odata/${fileId}/data`;

export const FileIdLink = ({ fileId }: { fileId: string }) => (
    <a
        href={fileOdataUrl(fileId)}
        target="_blank"
        rel="noreferrer"
        className="text-blue-600 dark:text-blue-400 underline underline-offset-2 hover:text-blue-500"
        onClick={(e) => e.stopPropagation()}
        title="Open file (OData JSON) in a new tab"
    >
        {JSON.stringify(fileId)}
    </a>
);

// Pretty-print a JSON value as React nodes so the `file_id` field's own value can
// be rendered as a link — the link is built from the field value directly (path +
// value), no string/regex scanning, so any id format (uuid or file_… prefixed) works.
// Shared by the By Node cards and the By Event payload view.
const INDENT = "  ";
export const renderJson = (value: unknown, level: number, fieldKey?: string): ReactNode => {
    if (fieldKey === "file_id" && typeof value === "string" && value.length > 0) {
        return <FileIdLink fileId={value} />;
    }
    if (value === null || value === undefined) return "null";
    if (typeof value === "string") return JSON.stringify(value);
    if (typeof value === "number" || typeof value === "boolean") return String(value);

    const pad = INDENT.repeat(level);
    const padInner = INDENT.repeat(level + 1);

    if (Array.isArray(value)) {
        if (value.length === 0) return "[]";
        return (
            <>
                {"[\n"}
                {value.map((item, i) => (
                    <Fragment key={i}>
                        {padInner}
                        {renderJson(item, level + 1)}
                        {i < value.length - 1 ? "," : ""}
                        {"\n"}
                    </Fragment>
                ))}
                {pad}
                {"]"}
            </>
        );
    }

    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    return (
        <>
            {"{\n"}
            {entries.map(([k, v], i) => (
                <Fragment key={k}>
                    {padInner}
                    {JSON.stringify(k)}
                    {": "}
                    {renderJson(v, level + 1, k)}
                    {i < entries.length - 1 ? "," : ""}
                    {"\n"}
                </Fragment>
            ))}
            {pad}
            {"}"}
        </>
    );
};
