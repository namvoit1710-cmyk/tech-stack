import type { INodeDefinition } from "@/features/node-definitions/types/node-definition";
import { useLanguage } from "@/hooks/use-language";
import { Card } from "@ldc/ui/components/card";
import { Button } from "@ldc/ui/components/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@ldc/ui/components/dropdown-menu";
import { DynamicNodeIcon } from "@ldc/workflow-editor";
import { CopyIcon, MoreVerticalIcon, PencilIcon, Trash2Icon } from "lucide-react";
import { useNavigate } from "react-router-dom";

interface NodeDefinitionCardProps {
    definition: INodeDefinition;
    onClone: (definition: INodeDefinition) => void;
    onDelete: (definition: INodeDefinition) => void;
}

const NodeDefinitionCard = ({ definition, onClone, onDelete }: NodeDefinitionCardProps) => {
    const { t } = useLanguage();
    const navigate = useNavigate();
    const open = () => navigate(`/node-definitions/${definition.id}`);

    return (
        <Card className="group relative flex flex-col gap-3 p-4 hover:shadow-md transition-shadow cursor-pointer" onClick={open}>
            <div className="flex items-start gap-3">
                <div
                    className="p-2 rounded-md shrink-0"
                    style={{
                        color: definition.color || "#3b82f6",
                        backgroundColor: `color-mix(in srgb, ${definition.color || "#3b82f6"} 10%, transparent)`,
                    }}
                >
                    <DynamicNodeIcon name={definition.icon || "box"} fallbackIconName="box" className="size-5" />
                </div>
                <div className="flex flex-col min-w-0 flex-1">
                    <span className="font-medium truncate">{definition.name}</span>
                    {definition.description && (
                        <span className="text-xs text-muted-foreground line-clamp-2">{definition.description}</span>
                    )}
                </div>

                <div onClick={(e) => e.stopPropagation()}>
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-7 opacity-0 group-hover:opacity-100 focus-visible:opacity-100" aria-label={t("node_definitions.actions")}>
                                <MoreVerticalIcon className="size-4" />
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem onSelect={open}>
                                <PencilIcon className="size-4 mr-2" />{t("node_definitions.edit")}
                            </DropdownMenuItem>
                            <DropdownMenuItem onSelect={() => onClone(definition)}>
                                <CopyIcon className="size-4 mr-2" />{t("node_definitions.clone")}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" onSelect={() => onDelete(definition)}>
                                <Trash2Icon className="size-4 mr-2" />{t("node_definitions.delete")}
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
            </div>

            <div className="mt-auto flex flex-col gap-2">
                <span className="text-xs text-muted-foreground font-mono truncate">{definition.base_worker_id}</span>
                {definition.tags && definition.tags.length > 0 && (
                    <div className="flex gap-1 flex-wrap">
                        {definition.tags.map((tag) => (
                            <span key={tag} className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground">{tag}</span>
                        ))}
                    </div>
                )}
            </div>
        </Card>
    );
};

export default NodeDefinitionCard;
