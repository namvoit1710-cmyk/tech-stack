import { IField, ISchema } from "../../../types/schema";
import { ActionConfig } from "../types";

function collectFromFields(fields: IField[], result: Map<string, ActionConfig>): void {
    for (const field of fields) {
        const actions = field.fieldConfig?.wrapperProps?.actions as ActionConfig[] | undefined;
        if (actions?.length) {
            for (const action of actions) {
                result.set(action.action, action);
            }
        }

        const controlAction = field.fieldConfig?.controlProps?.actionConfig as ActionConfig | undefined;
        if (controlAction) {
            result.set(controlAction.action, controlAction);
        }

        if (field.fields?.length) {
            collectFromFields(field.fields, result);
        }
    }
}

export function collectActions(schema: ISchema | undefined): ActionConfig[] {
    if (!schema?.fields?.length) return [];

    const map = new Map<string, ActionConfig>();
    collectFromFields(schema.fields, map);

    return Array.from(map.values());
}