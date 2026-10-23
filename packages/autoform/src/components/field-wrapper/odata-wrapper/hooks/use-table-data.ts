import { useMemo } from "react";
import type { ChangeRecord } from "./use-change-tracker";

export type RowDisplayStatus = "inserted" | "updated" | "deleted" | "none";

export interface DisplayRow {
    _id: string;
    __status: RowDisplayStatus;
    __disabled: boolean;
    [key: string]: any;
}

interface UseTableDataOptions {
    serverData: any[];
    changeMapRef: React.RefObject<Map<string, ChangeRecord>>;
    version: number;
}

export const useTableData = ({
    serverData,
    changeMapRef,
    version,
}: UseTableDataOptions) => {
    const tableData = useMemo<DisplayRow[]>(() => {
        const changeMap = changeMapRef.current;

        const insertedRows: DisplayRow[] = [];
        changeMap.forEach((record) => {
            if (record._action === "insert") {
                insertedRows.unshift({
                    ...record,
                    __status: "inserted",
                    __disabled: false,
                });
            }
        });

        const serverRows: DisplayRow[] = serverData.map((row) => {
            const id = row.__row_id ?? row._id ?? row.ID;
            const change = changeMap.get(String(id));

            if (!change) {
                return { ...row, _id: String(id), __status: "none", __disabled: false };
            }

            if (change._action === "delete") {
                return { ...row, _id: String(id), __status: "deleted", __disabled: true };
            }

            if (change._action === "edit") {
                const { _id, _action, ...updatedFields } = change;
                return { ...row, ...updatedFields, _id: String(id), __status: "edited", __disabled: false };
            }

            return { ...row, _id: String(id), __status: "none", __disabled: false };
        });

        return [...insertedRows, ...serverRows];
    }, [serverData, version]);

    return { tableData };
};