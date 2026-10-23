import { zodResolver } from "@hookform/resolvers/zod";
import type {
    ComponentType
} from "react";
import {
    forwardRef,
    useCallback,
    useEffect,
    useImperativeHandle,
    useMemo,
    useRef,
    useState,
} from "react";
import type {
    FieldValues
} from "react-hook-form";
import {
    FormProvider, useForm,
    useFormState, useWatch,
} from "react-hook-form";
import type { BuilderServices } from "../../contexts/builder.context";
import { BuilderProvider, useOptionalBuilderContext } from "../../contexts/builder.context";
import type { BuilderSubmitValidationState } from "../../types/builder-submit-validation-state";
import type { FieldComponentProps, FieldWrapperProps, IField, ISchema } from "../../types/schema";
import { cleanOutput } from "../../utils/clean-output";
import { getDefaultValues, normalizeArrayTableValues } from "../../utils/helpers";
import { normalizeFormErrors } from "../../utils/normalize-form-errors";
import { createZodSchema } from "../../utils/validation";
import { FieldControl } from "../field-control";
import { FieldWrapper } from "../field-wrapper";
import BuilderField from "./builder-field";

interface IBuilderProps {
    schema: ISchema;
    services?: BuilderServices;
    defaultValues?: FieldValues;
    readonly?: boolean;

    fieldControl?: Record<string, ComponentType<FieldComponentProps> | null>;
    fieldWrapper?: Record<string, ComponentType<FieldWrapperProps> | null>;

    onSubmit?: (values: FieldValues) => void;
    onValuesChange?: (values: FieldValues) => void;
    onValidChange?: (isValid: boolean) => void;
    onFormActions?: (action: string, payload?: Record<string, unknown>) => Promise<void> | void | Record<string, unknown> | null;
}

export interface BuilderRef {
    getPreviousValues: () => FieldValues;
    setPreviousValues: (values: FieldValues) => void;
    getValues?: () => FieldValues;
    onSubmit: () => void;
    validateAndGetFormState: () => Promise<BuilderSubmitValidationState>;
    getMethods: () => ReturnType<typeof useForm>;
    setRefresh: () => void;
    getRefetchRegistry?: () => {
        get: (key: string) => (() => Promise<void>) | undefined;
        getAll: () => Map<string, () => Promise<void>>;
        register: (key: string, fn: () => Promise<void>) => void;
        unregister: (key: string) => void;
    };
}

const Builder = forwardRef<BuilderRef, IBuilderProps>((props, ref) => {
    const {
        defaultValues,
        schema,
        services,
        readonly,

        fieldControl: customFieldControl = {},
        fieldWrapper: customFieldWrapper = {},

        onSubmit,
        onValuesChange,
        onValidChange,
        onFormActions
    } = props;
    // SA-2184 — a Builder nested inside another Builder (the Form Builder's LIVE
    // PREVIEW card, a form rendered inside a node-config form) installs its own
    // BuilderProvider, which would SHADOW the host's `services` and leave every
    // server-sourced control in that subtree with no transport. Inherit when the
    // caller does not pass its own.
    const hostContext = useOptionalBuilderContext();
    const resolvedServices = services ?? hostContext?.services;

    const [previousValues, setPreviousValues] = useState<FieldValues>({});
    const [refresh, setRefresh] = useState(false);
    const refetchMap = useRef(new Map<string, () => Promise<void>>());

    const registerRefetch = useCallback((key: string, fn: () => Promise<void>) => {
        refetchMap.current.set(key, fn);
    }, []);

    const unregisterRefetch = useCallback((key: string) => {
        refetchMap.current.delete(key);
    }, []);

    const zodSchema = useMemo(
        () => createZodSchema(schema.fields),
        [schema.fields]
    );

    const resolvedDefaultValues = useMemo(
        () => normalizeArrayTableValues(
            schema.fields,
            defaultValues ?? getDefaultValues(schema.fields),
        ) as FieldValues,
        [defaultValues, schema.fields]
    );
    const defaultValuesSignature = useMemo(
        () => JSON.stringify(resolvedDefaultValues),
        [resolvedDefaultValues]
    );

    const methods = useForm({
        defaultValues: resolvedDefaultValues,
        mode: "onChange",
        resolver: zodResolver(zodSchema)
    });

    const formValues: FieldValues = useWatch({ control: methods.control });
    const { isValid } = useFormState({ control: methods.control });

    const onBuilderSubmit = useCallback((values: FieldValues) => {
        const cleanedValues = cleanOutput(values) as FieldValues;
        onSubmit?.(cleanedValues);
    }, [onSubmit]);

    const validateAndGetFormState = useCallback(async (): Promise<BuilderSubmitValidationState> => {
        const isFormValid = await methods.trigger();
        let validationErrors = methods.formState.errors;

        if (!isFormValid) {
            await methods.handleSubmit(
                () => undefined,
                (currentErrors) => {
                    validationErrors = currentErrors;
                },
            )();
        }

        return {
            isValid: isFormValid,
            values: methods.getValues(),
            errors: normalizeFormErrors(validationErrors),
        };
    }, [methods]);

    useImperativeHandle(ref, () => ({
        onSubmit: methods.handleSubmit(onBuilderSubmit),
        getValues: () => methods.getValues(),
        validateAndGetFormState,
        getPreviousValues: () => { return previousValues },
        setPreviousValues: (values: FieldValues) => { setPreviousValues(values) },
        getMethods: () => { return methods },
        setRefresh: () => {
            setRefresh(true);
            setTimeout(() => setRefresh(false), 500);
        },
        getRefetchRegistry: () => ({
            get: (key: string) => refetchMap.current.get(key),
            getAll: () => refetchMap.current,
            register: registerRefetch,
            unregister: unregisterRefetch,
        }),
    }), [methods, onBuilderSubmit, previousValues, registerRefetch, unregisterRefetch, validateAndGetFormState]);

    const lastAppliedSignature = useRef<string | null>(null);
    useEffect(() => {
        // Only re-sync the form when the resolved defaults actually change in
        // content. Keying on the object reference re-fired this effect (and wiped
        // user state) on every identical-data refetch, and keepDirtyValues preserves
        // rows/edits the user added when the defaults genuinely change.
        if (lastAppliedSignature.current === defaultValuesSignature) return;
        lastAppliedSignature.current = defaultValuesSignature;
        methods.reset(resolvedDefaultValues, { keepDirtyValues: true });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [defaultValuesSignature]);

    useEffect(() => onValuesChange?.(formValues), [formValues, onValuesChange]);
    useEffect(() => onValidChange?.(isValid), [isValid, onValidChange]);

    return (
        <FormProvider {...methods}>
            <BuilderProvider
                value={{
                    schema,
                    services: resolvedServices,
                    refresh,
                    setRefresh,
                    readonly,
                    registerRefetch,
                    unregisterRefetch,
                    onFormActions: onFormActions,
                    wrapperComponent: {
                        ...customFieldWrapper,
                        ...(FieldWrapper as Record<string, React.ComponentType<FieldWrapperProps> | null>),
                    },
                    fieldComponent: {
                        ...customFieldControl,
                        ...FieldControl,
                    },
                }}
            >
                {schema.fields.map((field: IField) => {
                    return (
                        <BuilderField
                            key={field.key}
                            field={field}
                            path={[field.key]}
                        />
                    );
                })}
            </BuilderProvider>
        </FormProvider>
    );
});

Builder.displayName = "Builder";

export default Builder;
