import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { Spinner } from "@ldc/ui/components/spinner";
import { useEffect, useId, useState } from "react";
import { gatewayFunctionKey, useCreateGatewayFunction, useUpdateGatewayFunction } from "../../hooks/apis/gateway-functions";
import {
    HTTP_METHODS,
    HttpMethod,
    ICreateGatewayFunctionPayload,
    IGatewayFunction,
    IGatewayFunctionParam,
    IGatewayOutputField,
} from "../../types/gateway";
import { InputParamsBuilder, OutputFieldsBuilder } from "./param-schema-builder";

interface Props {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    app: string;
    /** Present = edit mode. */
    fn?: IGatewayFunction;
}

const GatewayFunctionModal = ({ open, onOpenChange, app, fn }: Props) => {
    const { t } = useLanguage();
    const uid = useId();
    const queryClient = useQueryClient();
    const isEdit = !!fn;

    const [name, setName] = useState("");
    const [method, setMethod] = useState<HttpMethod>("GET");
    const [pathTemplate, setPathTemplate] = useState("");
    const [description, setDescription] = useState("");
    const [timeoutMs, setTimeoutMs] = useState(30_000);
    const [tags, setTags] = useState("");
    const [params, setParams] = useState<IGatewayFunctionParam[]>([]);
    const [outputs, setOutputs] = useState<IGatewayOutputField[]>([]);

    useEffect(() => {
        if (!open) return;
        setName(fn?.name ?? "");
        setMethod(fn?.method ?? "GET");
        setPathTemplate(fn?.path_template ?? "");
        setDescription(fn?.description ?? "");
        setTimeoutMs(fn?.timeout_ms ?? 30_000);
        setTags((fn?.tags ?? []).join(", "));
        setParams(fn?.input_params ?? []);
        setOutputs(fn?.output_fields ?? []);
    }, [open, fn]);

    const invalidate = () => {
        queryClient.invalidateQueries({ queryKey: gatewayFunctionKey.list(app) });
        if (fn) queryClient.invalidateQueries({ queryKey: gatewayFunctionKey.detail(app, fn.name) });
    };

    const { mutate: create, isPending: creating } = useCreateGatewayFunction({
        onSuccess: () => { toast.success(t("notification.success"), "Function created."); invalidate(); onOpenChange(false); },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Create failed."),
    });
    const { mutate: update, isPending: updating } = useUpdateGatewayFunction({
        onSuccess: () => { toast.success(t("notification.success"), "Function updated."); invalidate(); onOpenChange(false); },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Update failed."),
    });

    const busy = creating || updating;
    const canSave = !!name.trim() && !!pathTemplate.trim() && timeoutMs > 0;

    const submit = () => {
        if (!canSave) return;
        const payload: ICreateGatewayFunctionPayload = {
            name: name.trim(),
            method,
            path_template: pathTemplate.trim(),
            description,
            timeout_ms: timeoutMs,
            tags: tags.split(",").map((x) => x.trim()).filter(Boolean),
            input_params: params,
            output_fields: outputs,
        };
        if (isEdit && fn) update({ app, name: fn.name, payload });
        else create({ app, payload });
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>{isEdit ? "Edit function" : "Add function"}</DialogTitle>
                    <DialogDescription>Define a callable function on this app. Path params must match {"{placeholders}"} in the path template.</DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-4 py-1">
                    <div className="grid grid-cols-3 gap-3">
                        <div className="col-span-2 flex flex-col gap-1.5">
                            <Label htmlFor={`${uid}-name`}>Name <span className="text-destructive">*</span></Label>
                            <Input id={`${uid}-name`} value={name} onChange={(e) => setName(e.target.value)} placeholder="getOrder" />
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <Label>Method</Label>
                            <Select value={method} onValueChange={(v) => setMethod(v as HttpMethod)}>
                                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {HTTP_METHODS.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor={`${uid}-path`}>Path template <span className="text-destructive">*</span></Label>
                        <Input id={`${uid}-path`} value={pathTemplate} onChange={(e) => setPathTemplate(e.target.value)} placeholder="/orders/{order_id}" />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor={`${uid}-timeout`}>Timeout (ms)</Label>
                            <Input id={`${uid}-timeout`} type="number" min={1} value={timeoutMs} onChange={(e) => setTimeoutMs(Number(e.target.value))} />
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor={`${uid}-tags`}>Tags</Label>
                            <Input id={`${uid}-tags`} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="orders, read" />
                        </div>
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor={`${uid}-desc`}>Description</Label>
                        <textarea
                            id={`${uid}-desc`} value={description} onChange={(e) => setDescription(e.target.value)} rows={2}
                            className="w-full text-sm p-2 border border-border rounded-md resize-y bg-transparent focus:outline-none focus:ring-2 focus:ring-ring"
                        />
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label>Input parameters</Label>
                        <InputParamsBuilder value={params} onChange={setParams} />
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label>Output fields</Label>
                        <OutputFieldsBuilder value={outputs} onChange={setOutputs} />
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>{t("cancel")}</Button>
                    <Button onClick={submit} disabled={!canSave || busy}>
                        {busy && <Spinner className="size-4 mr-2" />}{t("save")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default GatewayFunctionModal;
