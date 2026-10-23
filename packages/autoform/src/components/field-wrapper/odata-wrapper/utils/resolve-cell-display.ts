interface OptionItem {
    label: string;
    value: string | number;
    [key: string]: unknown;
}

type DisplayPrimitive = string | number | boolean | null | undefined;
type DisplayValue = DisplayPrimitive | DisplayPrimitive[];

interface ResolveTableCellDisplayValueInput {
    value: DisplayValue;
    options?: unknown[];
    valueKey?: string;
    labelKey?: string;
    shouldResolveOptions: boolean;
}

const NUMBER_FORMATTER = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 20,
});

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
});

const ISO_DATE_TIME_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:\d{2})$/;

const normalizeOptions = (raw: unknown[]): OptionItem[] => {
    if (!Array.isArray(raw)) return [];

    return raw.map((option) => {
        if (typeof option === "string" || typeof option === "number") {
            return { label: String(option), value: option };
        }

        const item = option as Record<string, unknown>;
        const rawLabel = item.label ?? item.name ?? item.text ?? item.value ?? item.id;
        const label =
            typeof rawLabel === "string" ||
            typeof rawLabel === "number" ||
            typeof rawLabel === "boolean"
                ? String(rawLabel)
                : "";

        return {
            label,
            value: (item.value ?? item.id ?? item.code ?? "") as string | number,
            ...item,
        };
    });
};

const resolveOptionLabel = (
    value: DisplayValue,
    options: OptionItem[],
    valueKey = "id",
    labelKey = "value",
): DisplayValue => {
    const toDisplayPrimitive = (candidate: unknown, fallback: DisplayPrimitive): DisplayPrimitive => {
        if (
            typeof candidate === "string" ||
            typeof candidate === "number" ||
            typeof candidate === "boolean" ||
            candidate == null
        ) {
            return candidate;
        }

        return fallback;
    };

    if (options.length === 0) {
        return value;
    }

    if (Array.isArray(value)) {
        return value.map((entry) => {
            const found = options.find((option) => String(option[valueKey]) === String(entry));
            return toDisplayPrimitive(found?.[labelKey], entry);
        });
    }

    const found = options.find((option) => String(option[valueKey]) === String(value));
    return toDisplayPrimitive(found?.[labelKey], value);
};

const formatPrimitiveValue = (value: unknown): unknown => {
    if (typeof value === "number") {
        return NUMBER_FORMATTER.format(value);
    }

    if (typeof value === "string" && ISO_DATE_TIME_PATTERN.test(value)) {
        const date = new Date(value);

        if (!Number.isNaN(date.getTime())) {
            return DATE_TIME_FORMATTER.format(date);
        }
    }

    return value;
};

const formatDisplayValue = (value: DisplayValue): DisplayValue => {
    if (Array.isArray(value)) {
        return value.map((entry) => formatPrimitiveValue(entry) as DisplayPrimitive);
    }

    return formatPrimitiveValue(value) as DisplayPrimitive;
};

export const resolveTableCellDisplayValue = ({
    value,
    options = [],
    valueKey,
    labelKey,
    shouldResolveOptions,
}: ResolveTableCellDisplayValueInput): DisplayValue => {
    const normalizedOptions = normalizeOptions(options);
    const resolvedValue = shouldResolveOptions
        ? resolveOptionLabel(value, normalizedOptions, valueKey, labelKey)
        : value;

    return formatDisplayValue(resolvedValue);
};
