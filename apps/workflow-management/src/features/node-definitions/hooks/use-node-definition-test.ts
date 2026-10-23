// One shared test run for the builder's Before/After tabs. A single call to
// POST /node-definitions/{id}/test returns both `resolved_input` (Before) and `outputs` (After),
// so both tabs read from the same result. Keeps the 422 (not-testable) handling and per-definition
// localStorage persistence that previously lived in the Test tab.
import { useTestNodeDefinition } from "@/features/node-definitions/hooks/apis/node-definitions";
import { ITestNodeDefinitionResponse } from "@/features/node-definitions/types/node-definition";
import { useLanguage } from "@/hooks/use-language";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { useCallback, useRef, useState } from "react";

const NOT_TESTABLE_CODE = "NODE_NOT_TESTABLE_STANDALONE";
const lastInputKey = (id: string) => `nd-test:${id}`;

const readLastInput = (id: string): Record<string, any> => {
    if (typeof window === "undefined") return {};
    try {
        return JSON.parse(window.localStorage.getItem(lastInputKey(id)) || "{}") || {};
    } catch {
        return {};
    }
};

export interface NodeDefinitionTest {
    result: ITestNodeDefinitionResponse | null;
    error: string | null;
    notTestable: boolean;
    isPending: boolean;
    /** Last sample input used (prefilled from localStorage). */
    lastInput: Record<string, any>;
    /** Run the whole pipeline with the given sample input (fills resolved_input + outputs). */
    run: (values: Record<string, any>) => void;
    /** Re-run with the last sample input (used by the After tab). */
    runLast: () => void;
}

export function useNodeDefinitionTest(definitionId: string): NodeDefinitionTest {
    const { t } = useLanguage();
    const [result, setResult] = useState<ITestNodeDefinitionResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [notTestable, setNotTestable] = useState(false);
    const [lastInput, setLastInput] = useState<Record<string, any>>(() => readLastInput(definitionId));
    const lastInputRef = useRef(lastInput);

    const { mutate, isPending } = useTestNodeDefinition({
        onSuccess: (data) => { setResult(data.data); setError(null); setNotTestable(false); },
        onError: (err: any) => {
            const body = err.response?.data;
            if (body?.error?.code === NOT_TESTABLE_CODE) {
                setNotTestable(true);
                setError(null);
                setResult(null);
                return;
            }
            setNotTestable(false);
            setResult(null);
            const message = body?.error?.message || body?.errors?.join?.(", ") || err.message || t("node_definition_builder.test_failed");
            setError(message);
            toast.error(t("notification.error"), message);
        },
    });

    const run = useCallback((values: Record<string, any>) => {
        lastInputRef.current = values;
        setLastInput(values);
        if (typeof window !== "undefined") {
            try { window.localStorage.setItem(lastInputKey(definitionId), JSON.stringify(values)); } catch { /* ignore */ }
        }
        mutate({ id: definitionId, payload: { input_data: values } });
    }, [definitionId, mutate]);

    const runLast = useCallback(() => run(lastInputRef.current || {}), [run]);

    return { result, error, notTestable, isPending, lastInput, run, runLast };
}
