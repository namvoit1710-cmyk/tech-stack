import { useCallback } from "react";
import { useFormContext } from "react-hook-form";

export type TableDataChangeType = "cell-change" | "insert" | "remove" | "reset";

interface UseTableSyncOptions {
    name: string;
}

export const useTableSync = ({ name }: UseTableSyncOptions) => {
    const { setValue } = useFormContext();

    const syncArrayToRHF = useCallback((data: any[], changeType: TableDataChangeType = "cell-change") => {
        const clean = data.map(({ _id, ...rest }) => rest);
        
        const shouldValidate = changeType === "cell-change";
        
        setValue(name, clean, { shouldDirty: true, shouldValidate });
    }, [name, setValue]);

    return { syncArrayToRHF };
};
