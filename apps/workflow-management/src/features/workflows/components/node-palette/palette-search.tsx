import { useLanguage } from "@/hooks/use-language"
import { Input } from "@ldc/ui/components/input"
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback"
import { SearchIcon } from "lucide-react"

interface IPaletteSearchProps {
    onChange: (value: string) => void
    /** SA-1930: the catalog size, so the placeholder can say what is searchable. */
    total?: number
}

const PaletteSearch = ({ onChange, total }: IPaletteSearchProps) => {
    const { t } = useLanguage()

    const debouncedOnChange = useDebounceCallback(onChange, 500)

    return (
        <div className="px-4 relative">
            <Input
                className="w-full pr-8 h-9"
                placeholder={total ? t("search_node_placeholder_count", { total }) : t("search_node_placeholder")}
                onChange={(e) => debouncedOnChange(e.target.value)}
            />

            <SearchIcon className="absolute right-6 top-1/2 -translate-y-1/2 size-4" strokeWidth={1.5} />
        </div>
    )
}

export default PaletteSearch