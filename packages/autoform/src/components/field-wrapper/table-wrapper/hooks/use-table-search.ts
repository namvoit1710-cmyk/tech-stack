import { useDebounceValue } from "@ldc/ui/hooks/use-debounce-value";
import { useMemo, useState } from "react";

interface SearchConfig {
    placeholder?: string;
    searchColumn?: string[];
    searchColumns?: string[];
}

interface UseTableSearchOptions {
    tableData: any[];
    searchConfig?: SearchConfig;
    isSyncServer?: boolean;
}

export const useTableSearch = ({ tableData, searchConfig, isSyncServer }: UseTableSearchOptions) => {
    const [searchValue, setSearchValue] = useState("");
    const [debouncedValue, updateDebouncedValue] = useDebounceValue("", 500);

    const updateSearch = (value: string) => {
        setSearchValue(value);
        updateDebouncedValue(value);
    }

    const clearSearch = () => {
        setSearchValue("");
        updateDebouncedValue("");
    }

    const filteredData = useMemo(() => {
        if (isSyncServer) return tableData;

        const searchTerm = (debouncedValue || "").trim().toLowerCase();
        if (!searchTerm) return tableData;

        let searchCols = searchConfig?.searchColumn || searchConfig?.searchColumns || [];

        // Fallback: if no columns specified, search all keys in the first row
        if (searchCols.length === 0 && tableData.length > 0) {
            searchCols = Object.keys(tableData[0]).filter(key => !key.startsWith("_"));
        }

        if (!searchCols.length) return tableData;

        return tableData.filter((row: any) =>
            searchCols.some((column) => {
                const value = row?.[column];
                if (value === null || value === undefined) return false;
                return String(value).toLowerCase().includes(searchTerm);
            })
        );
    }, [tableData, debouncedValue, searchConfig, isSyncServer]);

    return { filteredData, updateSearch, searchValue, clearSearch, placeholder: searchConfig?.placeholder };
};
