import { TYPE_CONFIG_PARAMETER } from "@/constants/config";

export interface IConfigParameter {
    key: string;
    label: string;
    description?: string;
    type: TYPE_CONFIG_PARAMETER;
    required?: boolean;
    default?: undefined | null | string;
}

export interface IConfigParametersResponse {
    ok: boolean;
    parameters: IConfigParameter[];
}
