import type { INodeOutputScope } from "../types/execution"
import type { IExprSuggestion, IScopedVariable, IScopedVariablePath } from "../types/node-detail"
import type { IDesignTimeSuggestionMatch, IDesignTimeSuggestionScopeSummary, IVariableSuggestionPath, IVariableSuggestionScope } from "../types/workflows"

export const transformNodeOutputScopesToScopedVariables = (
    scopes: INodeOutputScope[]
): IScopedVariable[] => scopes.map(scope => ({
    label: scope.label,
    scopeType: scope.scope_type,
    scopeId: scope.scope_id,
    expressionPrefix: scope.expression_prefix,
    paths: (scope.paths ?? []).reduce<Record<string, IScopedVariablePath>>((acc, pathItem) => {
        acc[pathItem.label] = {
            sample: pathItem.sample,
            displayPath: pathItem.display_path,
            kind: pathItem.kind,
            outputType: pathItem.output_type,
        }

        return acc
    }, {}),
    artifacts: scope.artifacts ?? [],
    hasFileData: scope.has_file_data ?? false,
    fileId: scope.file_id ?? null,
    columns: scope.columns ?? [],
    previewRows: scope.preview_rows ?? [],
}))

// Fold the design-time endpoint's FLAT matches[] into the grouped IVariableSuggestionScope[] shape
// that the run-scoped path already produces, so the existing panel/transform render it unchanged.
export const normalizeDesignTimeMatchesToScopes = (
    matches: IDesignTimeSuggestionMatch[],
    scopeSummaries: IDesignTimeSuggestionScopeSummary[]
): IVariableSuggestionScope[] => {
    const summaryById = new Map(scopeSummaries.map(s => [s.scope_id, s]))
    const order: string[] = []
    const pathsByScope = new Map<string, IVariableSuggestionPath[]>()
    const metaByScope = new Map<string, { scope_type: string; label: string; expression_prefix: string; node_type: string | null }>()

    for (const match of matches) {
        if (!pathsByScope.has(match.scope_id)) {
            pathsByScope.set(match.scope_id, [])
            order.push(match.scope_id)
            const summary = summaryById.get(match.scope_id)
            metaByScope.set(match.scope_id, {
                scope_type: match.scope_type,
                label: summary?.label ?? match.scope_label,
                expression_prefix: summary?.expression_prefix ?? match.expression_prefix,
                node_type: match.node_type,
            })
        }
        pathsByScope.get(match.scope_id)!.push({
            path: match.path,
            kind: match.kind,
            output_type: match.output_type,
            item_type: match.item_type,
            sample: undefined,
            columns: [],
            preview_rows: [],
            label: match.label,
            expandable: false,
            display_path: match.display_path,
        })
    }

    return order.map(scopeId => {
        const info = metaByScope.get(scopeId)!
        return {
            scope_type: info.scope_type,
            scope_id: scopeId,
            label: info.label,
            expression_prefix: info.expression_prefix,
            paths: pathsByScope.get(scopeId) ?? [],
            node_type: info.node_type,
            has_file_data: false,
            file_id: null,
            columns: [],
            preview_rows: [],
            artifacts: [],
            default_artifact_path: null,
        }
    })
}

// Flatten scoped variables into ready-to-insert suggestions for the inline {{$...}} typeahead.
export const scopedVariablesToExprSuggestions = (
    scopedVariables: IScopedVariable[]
): IExprSuggestion[] => {
    const suggestions: IExprSuggestion[] = []
    for (const scope of scopedVariables) {
        for (const [label, pathItem] of Object.entries(scope.paths)) {
            if (!pathItem.displayPath) continue
            suggestions.push({
                value: pathItem.displayPath,
                label,
                description: pathItem.outputType,
                group: scope.label,
            })
        }
    }
    return suggestions
}

export const transformVariableSuggestionScopesToScopedVariables = (
    scopes: IVariableSuggestionScope[]
): IScopedVariable[] => scopes.map(scope => ({
    label: scope.label,
    scopeType: scope.scope_type,
    scopeId: scope.scope_id,
    expressionPrefix: scope.expression_prefix,
    paths: (scope.paths ?? []).reduce<Record<string, IScopedVariablePath>>((acc, pathItem) => {
        acc[pathItem.label] = {
            sample: pathItem.sample,
            displayPath: pathItem.display_path,
            kind: pathItem.kind,
            outputType: pathItem.output_type,
        }

        return acc
    }, {}),
    artifacts: scope.artifacts ?? [],
    hasFileData: scope.has_file_data ?? false,
    fileId: scope.file_id ?? null,
    columns: scope.columns ?? [],
    previewRows: scope.preview_rows ?? [],
}))
