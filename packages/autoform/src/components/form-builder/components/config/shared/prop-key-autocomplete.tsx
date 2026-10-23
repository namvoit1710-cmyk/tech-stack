import {
    Autocomplete,
    AutocompleteContent,
    AutocompleteInput,
    AutocompleteItem,
    AutocompleteList,
} from "@ldc/ui/blocks/autocomplete/autocomplete";

/**
 * The key cell of a raw prop row: a free-text input that also suggests the keys the
 * selected wrapper/control is known to read. Moved verbatim out of `config-panel.tsx`.
 */
export function PropKeyAutocomplete({
    value,
    suggestions,
    disabled,
    onChange,
}: {
    value: string;
    suggestions: string[];
    disabled?: boolean;
    onChange: (next: string) => void;
}) {
    return (
        <Autocomplete
            items={suggestions}
            value={value}
            onValueChange={(next: string) => onChange(next)}
            disabled={disabled}
        >
            <AutocompleteInput placeholder="key" className="h-8! text-xs! flex-1 min-w-0" />
            <AutocompleteContent>
                <AutocompleteList>
                    {(item: string) => (
                        <AutocompleteItem key={item} value={item}>
                            {item}
                        </AutocompleteItem>
                    )}
                </AutocompleteList>
            </AutocompleteContent>
        </Autocomplete>
    );
}
