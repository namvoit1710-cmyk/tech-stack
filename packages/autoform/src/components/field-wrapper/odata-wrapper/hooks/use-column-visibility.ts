import { useState } from "react";
import type { IField } from "../../../../types/schema";

interface IUseColumnVisibleProps {
    field: IField;
}

const useColumnVisible = ({
    field,
}: IUseColumnVisibleProps) => {

    const subFields: IField | undefined = field?.fields?.[0];
    const columns = (subFields?.fields ?? [])?.map((subField) => ({
        key: (subField.fieldConfig?.controlProps?.aliasKey ?? subField.key).replace(/\./g, "_"),
        label: subField.fieldConfig?.wrapperProps?.label || subField.key,
        hidden: subField.fieldConfig?.wrapperProps?.hidden === true,
    }));

    const initialVisibility = columns.reduce((acc, col) => {
        acc[col.key] = !col.hidden;
        return acc;
    }, {} as Record<string, boolean>);

    const [visibleColumns, setVisibleColumns] = useState<Record<string, boolean>>(initialVisibility);

    return {
        columns,
        visibleColumns,
        setVisibleColumns,
    }
}

export default useColumnVisible;
