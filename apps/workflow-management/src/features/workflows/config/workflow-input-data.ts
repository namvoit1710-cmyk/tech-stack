import { IField } from "@common/components/ldc-auto-form/interfaces/component.interface";

export const workflowInputData: IField[] = [
    {
        key: "input_data",
        outputType: "string",
        default: "{}",
        fieldConfig: {
            fieldControl: "CodeControl",
            controlProps: {
                placeholder: "Please input the data for workflow execution",
            },
            fieldWrapper: "FormItemWrapper",
            wrapperProps: {
                label: "Workflow Run Input Data"
            }
        }
    }
]