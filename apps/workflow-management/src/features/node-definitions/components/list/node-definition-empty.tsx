import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import { BoxesIcon, PlusIcon } from "lucide-react";

interface NodeDefinitionEmptyProps {
    /** True when the list is empty because of a search/filter, not because there are none. */
    filtered?: boolean;
    onCreate: () => void;
}

const NodeDefinitionEmpty = ({ filtered, onCreate }: NodeDefinitionEmptyProps) => {
    const { t } = useLanguage();

    return (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
            <div className="p-4 rounded-full bg-muted text-muted-foreground">
                <BoxesIcon className="size-8" strokeWidth={1.25} />
            </div>
            <div className="flex flex-col gap-1">
                <h3 className="text-base font-medium">
                    {filtered ? t("node_definitions.empty_filtered_title") : t("node_definitions.empty_title")}
                </h3>
                <p className="text-sm text-muted-foreground max-w-sm">
                    {filtered ? t("node_definitions.empty_filtered_desc") : t("node_definitions.empty_desc")}
                </p>
            </div>
            {!filtered && (
                <Button onClick={onCreate} className="mt-2">
                    <PlusIcon className="size-4 mr-1" />
                    {t("node_definitions.empty_cta")}
                </Button>
            )}
        </div>
    );
};

export default NodeDefinitionEmpty;
