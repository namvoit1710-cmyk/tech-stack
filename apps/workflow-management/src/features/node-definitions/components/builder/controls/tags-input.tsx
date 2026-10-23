import { useLanguage } from "@/hooks/use-language";
import { Input } from "@ldc/ui/components/input";
import { XIcon } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

interface TagsInputProps {
    value: string[];
    onChange: (tags: string[]) => void;
    placeholder?: string;
}

/** Chip-based tags input — add on Enter or comma, remove with ✕ or Backspace. */
const TagsInput = ({ value, onChange, placeholder }: TagsInputProps) => {
    const { t } = useLanguage();
    const [draft, setDraft] = useState("");

    const addTag = (raw: string) => {
        const tag = raw.trim();
        if (tag && !value.includes(tag)) onChange([...value, tag]);
        setDraft("");
    };

    const removeTag = (tag: string) => onChange(value.filter((v) => v !== tag));

    const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            addTag(draft);
        } else if (e.key === "Backspace" && !draft && value.length) {
            removeTag(value[value.length - 1]!);
        }
    };

    return (
        <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-border px-2 py-1.5 focus-within:ring-2 focus-within:ring-ring">
            {value.map((tag) => (
                <span key={tag} className="flex items-center gap-1 px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground">
                    {tag}
                    <button
                        type="button"
                        onClick={() => removeTag(tag)}
                        aria-label={t("node_definition_builder.tag_remove", { tag })}
                        className="hover:text-foreground"
                    >
                        <XIcon className="size-3" />
                    </button>
                </span>
            ))}
            <Input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleKeyDown}
                onBlur={() => draft && addTag(draft)}
                placeholder={value.length ? "" : placeholder}
                className="h-6 flex-1 min-w-24 border-0 shadow-none focus-visible:ring-0 px-1"
            />
        </div>
    );
};

export default TagsInput;
