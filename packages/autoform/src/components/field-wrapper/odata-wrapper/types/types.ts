export interface IODataParams {
    $top?: number;
    $skip?: number;
    $filter?: string;
    $search?: string;
    $orderby?: string;
    $count?: boolean;
    $select?: string;
}

export interface IODataResponse<T = any> {
    data: T[];
    file_id: string;
    total_matches: number;
}

export interface IODataService {
    fetch: <T = any>(endpoint: string, params?: IODataParams) => Promise<IODataResponse<T>>;
}