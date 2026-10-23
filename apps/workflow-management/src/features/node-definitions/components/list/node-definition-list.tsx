import { useMessageBox } from "@/components/providers/messagebox-provider";
import {
    nodeDefinitionKey,
    useCreateNodeDefinition,
    useDeleteNodeDefinition,
    useNodeDefinitionList,
} from "@/features/node-definitions/hooks/apis/node-definitions";
import type { ICreateNodeDefinitionPayload, INodeDefinition } from "@/features/node-definitions/types/node-definition";
import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { cn } from "@ldc/ui";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { nodepalleteKey } from "@/features/workflows/hooks/apis/node-pallete";
import { SearchIcon } from "lucide-react";
import { useMemo, useState } from "react";
import NodeDefinitionCard from "./node-definition-card";
import NodeDefinitionEmpty from "./node-definition-empty";

const ALL = "__all__";

interface NodeDefinitionListProps {
    /** Open the create popup (owned by the page). */
    onCreate: () => void;
}

const NodeDefinitionList = ({ onCreate }: NodeDefinitionListProps) => {
    const { t } = useLanguage();
    const queryClient = useQueryClient();
    const showMessageBox = useMessageBox();

    const [search, setSearch] = useState("");
    const [tagFilter, setTagFilter] = useState(ALL);
    const [workerFilter, setWorkerFilter] = useState(ALL);

    const { data, isLoading, isFetching, refetch } = useNodeDefinitionList({ tenant_id: "system" });
    const definitions = useMemo(() => data?.data?.items ?? [], [data]);

    const invalidate = () => {
        queryClient.invalidateQueries({ queryKey: nodeDefinitionKey.all });
        queryClient.invalidateQueries({ queryKey: nodepalleteKey.getAllNodePalletes() });
    };

    const { mutate: deleteDefinition, isPending: isDeleting } = useDeleteNodeDefinition({
        onSuccess: () => { toast.success(t("notification.success"), t("node_definitions.deleted_successfully")); invalidate(); refetch(); },
        onError: () => toast.error(t("notification.error"), t("node_definitions.delete_failed")),
    });

    const { mutate: cloneDefinition, isPending: isCloning } = useCreateNodeDefinition({
        onSuccess: () => { toast.success(t("notification.success"), t("node_definitions.cloned_successfully")); invalidate(); refetch(); },
        onError: () => toast.error(t("notification.error"), t("node_definitions.clone_failed")),
    });

    const handleDelete = async (d: INodeDefinition) => {
        const ok = await showMessageBox(t("node_definitions.confirm_delete", { name: d.name }), t("node_definitions.delete_title"));
        if (ok) deleteDefinition(d.id);
    };

    const handleClone = (d: INodeDefinition) => {
        const payload: ICreateNodeDefinitionPayload = {
            tenant_id: d.tenant_id || "system",
            name: `${d.name} (copy)`,
            description: d.description,
            base_type: d.base_type,
            base_worker_id: d.base_worker_id,
            locked_defaults: d.locked_defaults,
            input_schema: d.input_schema,
            output_schema: d.output_schema,
            input_mapping: d.input_mapping,
            output_mapping: d.output_mapping,
            icon: d.icon,
            color: d.color,
            tags: d.tags,
        };
        cloneDefinition(payload);
    };

    const tags = useMemo(() => Array.from(new Set(definitions.flatMap((d) => d.tags ?? []))).sort(), [definitions]);
    const workers = useMemo(() => Array.from(new Set(definitions.map((d) => d.base_worker_id).filter(Boolean))).sort(), [definitions]);

    const filtered = useMemo(() => {
        const q = search.toLowerCase();
        return definitions.filter((d) => {
            if (tagFilter !== ALL && !(d.tags ?? []).includes(tagFilter)) return false;
            if (workerFilter !== ALL && d.base_worker_id !== workerFilter) return false;
            if (!q) return true;
            return (
                d.name.toLowerCase().includes(q) ||
                d.description?.toLowerCase().includes(q) ||
                d.tags?.some((tag) => tag.toLowerCase().includes(q))
            );
        });
    }, [definitions, search, tagFilter, workerFilter]);

    const busy = isFetching || isDeleting || isCloning;
    const isFilteredEmpty = filtered.length === 0 && definitions.length > 0;

    return (
        <section className="flex flex-col gap-4 px-4 pb-6">
            <div className="flex flex-wrap items-center gap-2 justify-end">
                <div className="relative min-w-[280px] flex-1 max-w-md">
                    <Input className="pl-10" placeholder={t("node_definitions.search_placeholder")} value={search} onChange={(e) => setSearch(e.target.value)} />
                    <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
                </div>
                <Select value={tagFilter} onValueChange={setTagFilter}>
                    <SelectTrigger className="w-40"><SelectValue placeholder={t("node_definitions.filter_tag")} /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value={ALL}>{t("node_definitions.filter_all_tags")}</SelectItem>
                        {tags.map((tag) => <SelectItem key={tag} value={tag}>{tag}</SelectItem>)}
                    </SelectContent>
                </Select>
                <Select value={workerFilter} onValueChange={setWorkerFilter}>
                    <SelectTrigger className="w-48"><SelectValue placeholder={t("node_definitions.filter_worker")} /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value={ALL}>{t("node_definitions.filter_all_workers")}</SelectItem>
                        {workers.map((w) => <SelectItem key={w} value={w}>{w}</SelectItem>)}
                    </SelectContent>
                </Select>
            </div>

            {(isLoading || busy) && <div className="loader-bar w-full" />}

            {!isLoading && definitions.length === 0 && (
                <NodeDefinitionEmpty onCreate={onCreate} />
            )}

            {isFilteredEmpty && <NodeDefinitionEmpty filtered onCreate={onCreate} />}

            {filtered.length > 0 && (
                <div className={cn("grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 transition-opacity", busy && "opacity-60 pointer-events-none")}>
                    {filtered.map((d) => (
                        <NodeDefinitionCard key={d.id} definition={d} onClone={handleClone} onDelete={handleDelete} />
                    ))}
                </div>
            )}
        </section>
    );
};

export default NodeDefinitionList;
