import { useCallback } from "react";
import { useFormContext } from "react-hook-form";

export type TableDataChangeType = "cell-change" | "insert" | "remove" | "reset";

interface UseTableSyncOptions {
    name: string;
}

export const useTableSync = ({ name }: UseTableSyncOptions) => {
    const { setValue } = useFormContext();

    const syncArrayToRHF = useCallback((data: any[]) => {
        const clean = data?.map(({ _id, ...rest }) => rest);

        setValue(name, clean, { shouldDirty: true, shouldValidate: true });
    }, [name, setValue]);

    return { syncArrayToRHF };
};