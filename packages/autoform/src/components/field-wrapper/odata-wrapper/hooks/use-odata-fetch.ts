import { useCallback, useEffect, useState } from "react";
import type { BuilderServices } from "../../../../hooks/use-builder-services";

export interface IODataParams {
    $top?: number;
    $skip?: number;
    $count?: boolean;
    $filter?: string;
    $orderby?: string;
    $search?: string;
}

export interface IUseODataFetchProps {
    odataService?: BuilderServices["odata"];
    endpoint?: string;
    pagination: {
        pageIndex: number;
        pageSize: number;
    };
    searchValue?: string;
    defaultFilter?: string;
    columnFilter?: string;
    defaultOrderBy?: string;
}

export interface IUseODataFetchReturn<T = any> {
    data: T[];
    totalCount: number;
    loading: boolean;
    refetch: () => Promise<void>;
}

export const useODataFetch = <T = any>(
    props: IUseODataFetchProps
): IUseODataFetchReturn<T> => {
    const {
        odataService,
        endpoint,
        pagination,
        searchValue,
        defaultFilter,
        columnFilter,
        defaultOrderBy,
    } = props;

    const { pageIndex, pageSize } = pagination;

    const [data, setData] = useState<T[]>([]);
    const [totalCount, setTotalCount] = useState(0);
    const [loading, setLoading] = useState(false);

    const fetchData = useCallback(async () => {
        if (!odataService || !endpoint) return;

        setLoading(true);
        try {
            const params: IODataParams = {
                $top: pageSize,
                $skip: pageIndex * pageSize,
                $count: true,
            };

            const mergedFilter = [defaultFilter, columnFilter].filter(Boolean).join(' and ') || undefined;
            if (mergedFilter) params.$filter = mergedFilter;
            if (defaultOrderBy) params.$orderby = defaultOrderBy;
            if (searchValue) params.$search = searchValue;

            const res = await odataService.fetch?.(endpoint, params);
            setData(res?.data || []);
            setTotalCount(res?.total_count || 0);
        } catch (err) {
            console.error("OData fetch error:", err);
        } finally {
            setLoading(false);
        }
    }, [
        endpoint,
        pageIndex,
        pageSize,
        searchValue,
        defaultFilter,
        columnFilter,
        defaultOrderBy,
    ]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    return {
        data,
        totalCount,
        loading,
        refetch: fetchData,
    };
};