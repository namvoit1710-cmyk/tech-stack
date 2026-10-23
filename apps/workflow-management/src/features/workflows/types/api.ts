import { AxiosError } from "axios";

export interface IApiErrorBody {
    code?: string;
    errors?: string[];
}

export type IApiError = AxiosError<IApiErrorBody>;
