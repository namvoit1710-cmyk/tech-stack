import JsonView from "@/components/json-view/json-view"
import { useEditorStore } from "@/features/workflows/stores/editor-stores"
import type { IVariableSuggestionSource } from "@/features/workflows/types/workflows"
import { useLanguage } from "@/hooks/use-language"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@ldc/ui/components/accordion"
import { Paperclip, SquarePlay, Workflow } from "lucide-react"

interface IVariablesContextGroupProps {
    artifacts: IVariableSuggestionSource
}

const VariablesContextGroup = ({ artifacts }: IVariablesContextGroupProps) => {
    const { t } = useLanguage()

    const workflowInfo = useEditorStore(s => s.workflowInfo)

    return (
        <Accordion type="multiple" defaultValue={["workflow", "run-context", "artifacts"]}>
            <AccordionItem value="artifacts" className="border-0">
                <AccordionTrigger className="justify-start [&>svg]:order-first hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md">
                    <span className="pl-2 flex items-center gap-2">
                        <Paperclip className="size-4" />
                        <span>{t("artifacts")}</span>
                    </span>
                </AccordionTrigger>
                <AccordionContent className="pl-3 py-2">
                    {artifacts ? (
                        <div className="flex flex-col gap-2">
                            <JsonView
                                value={artifacts.paths.reduce((acc, path) => {
                                    if (!path.label) return acc

                                    acc[path.label] = path.sample
                                    return acc
                                }, {} as Record<string, unknown>)}
                                draggableKeys={true}
                                prefix={`${artifacts.expression_prefix}.#path}}`}
                            />
                        </div>
                    ) : (
                        <span className="text-sm text-gray-500 text-center w-full block">{t("no_artifacts")}</span>
                    )}
                </AccordionContent>
            </AccordionItem>

            <AccordionItem value="workflow" className="border-0">
                <AccordionTrigger className="justify-start [&>svg]:order-first hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md">
                    <span className="pl-2 flex items-center gap-2">
                        <Workflow className="size-4" />
                        <span>Workflow</span>
                    </span>
                </AccordionTrigger>
                <AccordionContent className="pl-3 py-2">
                    <JsonView
                        value={{
                            workflow_id: workflowInfo?.id,
                            workflow_name: workflowInfo?.name,
                        }}
                        draggableKeys={true}
                    />
                </AccordionContent>
            </AccordionItem>

            <AccordionItem value="run-context" className="border-0">
                <AccordionTrigger className="justify-start [&>svg]:order-first hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md">
                    <span className="pl-2 flex items-center gap-2">
                        <SquarePlay className="size-4" />
                        <span>Run Context</span>
                    </span>
                </AccordionTrigger>
                <AccordionContent className="pl-3 py-2">
                    <JsonView
                        value={{
                            test_run_id: workflowInfo?.test_run_id,
                        }}
                        draggableKeys={true}
                    />
                </AccordionContent>
            </AccordionItem>
        </Accordion>
    )
}

export default VariablesContextGroup