import { useCallback, useState } from "react";

export interface ISortState {
    field?: string;
    order?: "asc" | "desc";
}

export interface IPaginationState {
    pageIndex: number;
    pageSize: number;
}

export type IFilterState = Record<string, any>;

export interface IODataStateOptions {
    initialPageSize?: number;
    initialPageIndex?: number;
    initialSearchValue?: string;
    initialSort?: ISortState;
    initialFilters?: IFilterState;
}

export interface IODataStateReturn {
    // Pagination
    pagination: IPaginationState;
    setPagination: (
        pagination:
            | Partial<IPaginationState>
            | ((prev: IPaginationState) => Partial<IPaginationState> | IPaginationState)
    ) => void;

    // Search
    searchValue: string;
    setSearchValue: (value: string) => void;
    clearSearch: () => void;

    // Sort
    sortState: ISortState;
    setSortState: (sort: ISortState) => void;
    setSort: (field: string, order: "asc" | "desc") => void;

    // Filters
    filters: IFilterState;
    setFilters: (filters: IFilterState) => void;
    setFilter: (key: string, value: any) => void;
    removeFilter: (key: string) => void;
    clearFilters: () => void;

    // OData query string builders
    getOrderByString: () => string | undefined;
    getFilterString: () => string | undefined;

    // Global reset
    resetAll: () => void;
}

export const useODataState = (
    options: IODataStateOptions = {}
): IODataStateReturn => {
    const {
        initialPageSize = 10,
        initialPageIndex = 0,
        initialSearchValue = "",
        initialSort = {},
        initialFilters = {},
    } = options;

    const [pagination, _setPagination] = useState<IPaginationState>({
        pageIndex: initialPageIndex,
        pageSize: initialPageSize,
    });

    const [searchValue, setSearchValue] = useState<string>(initialSearchValue);

    const [sortState, setSortState] = useState<ISortState>(initialSort);

    const [filters, setFilters] = useState<IFilterState>(initialFilters);

    const setPagination = useCallback((
        newPagination:
            | Partial<IPaginationState>
            | ((prev: IPaginationState) => Partial<IPaginationState> | IPaginationState)
    ) => {
        if (typeof newPagination === 'function') {
            _setPagination(prev => {
                const result = newPagination(prev);
                return { ...prev, ...result };
            });
        } else {
            _setPagination(prev => ({ ...prev, ...newPagination }));
        }
    }, []);

    const setPageIndex = useCallback((pageIndex: number) => {
        _setPagination(prev => ({ ...prev, pageIndex }));
    }, []);

    // const setPageSize = useCallback((pageSize: number) => {
    //     _setPagination(prev => ({ ...prev, pageSize, pageIndex: 0 }));
    // }, []);

    const resetPagination = useCallback(() => {
        _setPagination({ pageIndex: initialPageIndex, pageSize: initialPageSize });
    }, [initialPageIndex, initialPageSize]);

    const clearSearch = useCallback(() => {
        setSearchValue("");
        setPageIndex(0);
    }, [setPageIndex]);

    const setSort = useCallback((field: string, order: "asc" | "desc") => {
        setSortState({ field, order });
        setPageIndex(0);
    }, [setPageIndex]);

    // const clearSort = useCallback(() => {
    //     setSortState({});
    // }, []);

    // const toggleSort = useCallback((field: string) => {
    //     setSortState(prev => {
    //         if (prev.field !== field) {
    //             return { field, order: "asc" };
    //         }
    //         if (prev.order === "asc") {
    //             return { field, order: "desc" };
    //         }
    //         return {};
    //     });
    //     setPageIndex(0);
    // }, [setPageIndex]);

    const setFilter = useCallback((key: string, value: any) => {
        setFilters(prev => ({ ...prev, [key]: value }));
        setPageIndex(0);
    }, [setPageIndex]);

    const removeFilter = useCallback((key: string) => {
        setFilters(prev => {
            const newFilters = { ...prev };
            delete newFilters[key];
            return newFilters;
        });
    }, []);

    const clearFilters = useCallback(() => {
        setFilters({});
        setPageIndex(0);
    }, [setPageIndex]);

    const getOrderByString = useCallback((): string | undefined => {
        if (!sortState.field) return undefined;
        return `${sortState.field} ${sortState.order || "asc"}`;
    }, [sortState]);

    const getFilterString = useCallback((): string | undefined => {
        const filterParts: string[] = [];

        Object.entries(filters).forEach(([key, value]) => {
            if (value === null || value === undefined || value === "") return;

            if (typeof value === "string") {
                filterParts.push(`contains(${key},'${value.replace(/'/g, "''")}')`);
            } else if (typeof value === "number") {
                filterParts.push(`${key} eq ${value}`);
            } else if (typeof value === "boolean") {
                filterParts.push(`${key} eq ${value}`);
            } else if (Array.isArray(value) && value.length > 0) {
                const inClause = value.map(v => `'${String(v).replace(/'/g, "''")}'`).join(",");
                filterParts.push(`${key} in (${inClause})`);
            }
        });

        return filterParts.length > 0 ? filterParts.join(" and ") : undefined;
    }, [filters]);

    const resetAll = useCallback(() => {
        resetPagination();
        setSearchValue(initialSearchValue);
        setSortState(initialSort);
        setFilters(initialFilters);
    }, [resetPagination, initialSearchValue, initialSort, initialFilters]);

    return {
        pagination,
        setPagination,

        searchValue,
        setSearchValue,
        clearSearch,

        setSort,
        sortState,
        setSortState,

        filters,
        setFilters,
        setFilter,
        removeFilter,
        clearFilters,

        getOrderByString,
        getFilterString,

        resetAll,
    };
};