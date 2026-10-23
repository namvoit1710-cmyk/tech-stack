import { nodepalleteKey } from "@/features/workflows/hooks/apis/node-pallete";
import { nodeDefinitionKey, useCreateNodeDefinition } from "@/features/node-definitions/hooks/apis/node-definitions";
import { useWorkerCatalog } from "@/features/node-definitions/hooks/use-worker-catalog";
import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { Spinner } from "@ldc/ui/components/spinner";
import { useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import ColorPicker from "../builder/controls/color-picker";
import IconPicker from "../builder/controls/icon-picker";
import TagsInput from "../builder/controls/tags-input";

interface NodeDefinitionCreateModalProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

/** Collects the must-have fields (name + base worker) plus optional basics, creates the node
 *  immediately, and opens the editor on the saved node so it's testable from the first screen. */
const NodeDefinitionCreateModal = ({ open, onOpenChange }: NodeDefinitionCreateModalProps) => {
    const { t } = useLanguage();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const uid = useId();
    const { workerOptions } = useWorkerCatalog();

    const [name, setName] = useState("");
    const [baseWorkerId, setBaseWorkerId] = useState("");
    const [description, setDescription] = useState("");
    const [icon, setIcon] = useState("");
    const [color, setColor] = useState("");
    const [tags, setTags] = useState<string[]>([]);

    const reset = () => {
        setName(""); setBaseWorkerId(""); setDescription(""); setIcon(""); setColor(""); setTags([]);
    };

    const { mutate: create, isPending } = useCreateNodeDefinition({
        onSuccess: (res: any) => {
            toast.success(t("notification.success"), t("node_definitions.created_successfully"));
            queryClient.invalidateQueries({ queryKey: nodeDefinitionKey.lists() });
            queryClient.invalidateQueries({ queryKey: nodepalleteKey.getAllNodePalletes() });
            onOpenChange(false);
            reset();
            navigate(`/node-definitions/${res.data.id}`);
        },
        onError: () => toast.error(t("notification.error"), t("node_definitions.create_failed")),
    });

    const canCreate = !!name.trim() && !!baseWorkerId;

    const submit = () => {
        if (!canCreate) return;
        create({
            tenant_id: "system",
            base_type: "TASK",
            base_worker_id: baseWorkerId,
            name: name.trim(),
            description,
            icon,
            color,
            tags,
        });
    };

    return (
        <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>{t("node_definitions.create_title")}</DialogTitle>
                    <DialogDescription>{t("node_definitions.create_modal_desc")}</DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-4 py-1">
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor={`${uid}-name`}>{t("node_definition_builder.info_name")} <span className="text-destructive">*</span></Label>
                        <Input
                            id={`${uid}-name`}
                            autoFocus
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder={t("node_definition_builder.info_name_placeholder")}
                            onKeyDown={(e) => { if (e.key === "Enter" && canCreate && !isPending) submit(); }}
                        />
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor={`${uid}-worker`}>{t("node_definition_builder.info_base_worker")} <span className="text-destructive">*</span></Label>
                        <Select value={baseWorkerId} onValueChange={setBaseWorkerId}>
                            <SelectTrigger id={`${uid}-worker`} className="w-full">
                                <SelectValue placeholder={t("node_definition_builder.info_base_worker_placeholder")} />
                            </SelectTrigger>
                            <SelectContent>
                                {workerOptions.map((w) => <SelectItem key={w.id} value={w.id}>{w.value}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor={`${uid}-desc`}>{t("node_definition_builder.info_description")}</Label>
                        <textarea
                            id={`${uid}-desc`}
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder={t("node_definition_builder.info_description_placeholder")}
                            rows={2}
                            className="w-full text-sm p-2 border border-border rounded-md resize-y bg-transparent focus:outline-none focus:ring-2 focus:ring-ring"
                        />
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="flex flex-col gap-1.5">
                            <Label>{t("node_definition_builder.info_icon")}</Label>
                            <IconPicker value={icon} color={color} onChange={setIcon} />
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <Label>{t("node_definition_builder.info_color")}</Label>
                            <ColorPicker value={color} onChange={setColor} />
                        </div>
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label>{t("node_definition_builder.info_tags")}</Label>
                        <TagsInput value={tags} onChange={setTags} placeholder={t("node_definition_builder.info_tags_placeholder")} />
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>{t("cancel")}</Button>
                    <Button onClick={submit} disabled={!canCreate || isPending}>
                        {isPending && <Spinner className="size-4 mr-2" />}
                        {t("node_definitions.create_submit")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default NodeDefinitionCreateModal;
