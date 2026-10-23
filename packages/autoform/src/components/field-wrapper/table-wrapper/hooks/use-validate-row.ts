import { useCallback, useState } from "react";

interface IUseValidateRowParams {
    onHasError?: () => void;
    onHasNoError?: () => void;
}

const useValidateRow = ({ onHasError, onHasNoError }: IUseValidateRowParams = {}) => {
    const [rowErrors, setRowErrors] = useState<Map<string, string>>(new Map());
    const [rowSuccess, setRowSuccess] = useState<Map<string, string>>(new Map());

    const clearRowError = useCallback((rowId: string) => {
        setRowErrors(prev => {
            const next = new Map(prev);
            next.delete(rowId);

            if (next.size === 0) {
                onHasNoError?.();
            }
            return next;
        });
    }, [onHasNoError])

    const addRowError = useCallback((rowId: string, message: string) => {
        setRowErrors(prev => new Map(prev).set(rowId, message));

        setRowSuccess(prev => {
            const next = new Map(prev);
            next.delete(rowId);
            return next;
        });

        onHasError?.();
    }, [onHasError])

    const clearErrorByRowIds = useCallback((rowIds: string[]) => {
        setRowErrors(prev => {
            const next = new Map(prev);
            rowIds.forEach(id => next.delete(id));

            if (next.size === 0) {
                onHasNoError?.();
            }
            return next;
        });
    }, [onHasNoError])

    const addRowSuccess = useCallback((rowId: string, message = "Success") => {
        setRowSuccess(prev => new Map(prev).set(rowId, message));

        setRowErrors(prev => {
            const next = new Map(prev);
            next.delete(rowId);
            if (next.size === 0) {
                onHasNoError?.();
            }
            return next;
        });
    }, [onHasNoError])

    const clearRowSuccess = useCallback((rowId: string) => {
        setRowSuccess(prev => {
            const next = new Map(prev);
            next.delete(rowId);
            return next;
        });
    }, [])

    const clearSuccessByRowIds = useCallback((rowIds: string[]) => {
        setRowSuccess(prev => {
            const next = new Map(prev);
            rowIds.forEach(id => next.delete(id));
            return next;
        });
    }, [])

    const getRowClassName = useCallback((row: any) => {
        const rowId = row.original._id;

        if (rowErrors.has(rowId)) {
            return "border border-destructive bg-destructive/10";
        }

        if (rowSuccess.has(rowId)) {
            return "border border-green-500 bg-green-50";
        }

        return "";
    }, [rowErrors, rowSuccess]);

    const getRowError = useCallback((rowId: string): string | undefined => {
        return rowErrors.get(rowId);
    }, [rowErrors]);

    return {
        addRowError,
        clearRowError,
        clearErrorByRowIds,
        addRowSuccess,
        clearRowSuccess,
        clearSuccessByRowIds,
        getRowClassName,
        getRowError,
    }
}

export default useValidateRow;