# Variable Suggestions Enhancement - Implementation Plan

## Requirements

1. **Transform API Response**: Use `paths` field from `scopes` items to generate `{label: sample}` structure
2. **Filter Artifacts**: Show only valid artifacts from each scope
3. **Remove UI Elements**: Remove Variables and Context accordions
4. **Redesign Execute Button**: Convert "Execute previous node" to icon button with horizontal layout showing node name

## API Response Structure

```typescript
interface VariableSuggestionResponse {
  data: {
    scopes: Array<{
      scope_type: string;          // "run", "workflow", "node", "variables", "artifacts", "input"
      scope_id: string;
      label: string;
      expression_prefix: string;
      paths: Array<{
        path: string;
        label: string;
        sample: any;
        display_path: string;
        kind: string;
        output_type: string;
      }>;
      artifacts: any[];
      has_file_data: boolean;
      file_id: string | null;
      columns: string[];
      preview_rows: any[];
    }>;
  };
}
```

## Files to Modify

### 1. Type Definitions
- **File**: `src/features/workflows/types/node-comprehensive.ts`
- **Changes**: 
  - Add `scopedVariables` to `IUseNodeDetailReturn`
  - Remove `artifactInputs` and `variablesInputs`

### 2. Custom Hook
- **File**: `src/features/workflows/hooks/use-node-detail.ts`
- **Changes**:
  - Add `scopedVariables` useMemo
  - Filter out "variables" and "artifacts" scope types
  - Transform paths to {label: sample} format
  - Update return object

### 3. UI Component
- **File**: `src/features/workflows/components/modals/nodes-detail-modal/detail-input.tsx`
- **Changes**:
  - Replace Variables/Context accordions with scope-based accordions
  - Add icon button + node name horizontal layout
  - Display paths as {label: sample}
  - Show artifacts list per scope

## Implementation Steps

### Step 1: Update Type Definitions

```typescript
// src/features/workflows/types/node-comprehensive.ts

export interface IScopedVariable {
  label: string;
  scopeType: string;
  scopeId: string;
  expressionPrefix: string;
  paths: Record<string, {
    sample: unknown;
    displayPath: string;
    kind: string;
    outputType: string;
  }>;
  artifacts: unknown[];
  hasFileData: boolean;
  fileId: string | null;
  columns: string[];
  previewRows: any[];
}

export interface IUseNodeDetailReturn {
  runId: string | undefined;
  selectedNode: any;
  currentNodeId: string | undefined;
  currentNodeStatus: NodeExecutionStatus;
  showOutputSchema: boolean;
  outputSchemaData: Record<string, any>;
  outputArtifacts: any[];
  nodeDetail: any;
  isNodeDetailLoading: boolean;
  refetchVariableInput: () => void;
  scopedVariables: IScopedVariable[];  // NEW
  isLoadingInput: boolean;
  isLoadingOutputSchema: boolean;
  // REMOVED: artifactInputs
  // REMOVED: variablesInputs
}
```

### Step 2: Update Custom Hook

```typescript
// src/features/workflows/hooks/use-node-detail.ts

import { useMemo } from "react"

const useNodeDetail = (): IUseNodeDetailReturn => {
    // ... existing code ...

    const { data: nodeVariableResponse, isLoading: isLoadingVariable, refetch } = 
        useGetNodeVariableSuggestions({
            runId: runId || "",
            nodeId: currentNodeId || "",
        }, {
            enabled: !!isOpenNodesPopup && !!runId && !!currentNodeId,
        })

    // NEW: Process scopes
    const scopedVariables = useMemo(() => {
        if (!nodeVariableResponse?.data?.scopes) return [];
        
        return nodeVariableResponse.data.scopes
            // Filter out Variables and Artifacts scopes
            .filter(scope => 
                scope.scope_type !== "variables" && 
                scope.scope_type !== "artifacts"
            )
            // Transform to simplified structure
            .map(scope => ({
                label: scope.label,
                scopeType: scope.scope_type,
                scopeId: scope.scope_id,
                expressionPrefix: scope.expression_prefix,
                // Transform paths array to {label: {sample, displayPath}} object
                paths: (scope.paths || []).reduce((acc, pathItem) => {
                    acc[pathItem.label] = {
                        sample: pathItem.sample,
                        displayPath: pathItem.display_path,
                        kind: pathItem.kind,
                        outputType: pathItem.output_type,
                    };
                    return acc;
                }, {} as Record<string, any>),
                artifacts: scope.artifacts || [],
                hasFileData: scope.has_file_data,
                fileId: scope.file_id,
                columns: scope.columns || [],
                previewRows: scope.preview_rows || [],
            }));
    }, [nodeVariableResponse]);

    return {
        runId,
        selectedNode,
        currentNodeId,
        currentNodeStatus,
        showOutputSchema,
        outputSchemaData: {},
        outputArtifacts: [],
        nodeDetail: nodeCatalogDetail?.data,
        isNodeDetailLoading,
        refetchVariableInput: refetch,
        scopedVariables,  // NEW
        isLoadingInput: isLoadingVariable,
        isLoadingOutputSchema,
    }
}
```

### Step 3: Update UI Component

```tsx
// src/features/workflows/components/modals/nodes-detail-modal/detail-input.tsx

import { Play } from "lucide-react"
import { Button } from "@ldc/ui/components/button"
import { 
    Accordion, 
    AccordionContent, 
    AccordionItem, 
    AccordionTrigger 
} from "@ldc/ui/components/accordion"
import useNodeDetail from "../../../hooks/use-node-detail"

const NodeDetailInput = () => {
    const { 
        scopedVariables, 
        isLoadingInput, 
        selectedNode,
        refetchVariableInput 
    } = useNodeDetail()

    const handleExecutePreviousNode = () => {
        // TODO: Implement execute logic
        console.log("Execute previous node")
    }

    if (isLoadingInput) {
        return (
            <div className="h-full flex items-center justify-center">
                <div>Loading...</div>
            </div>
        )
    }

    return (
        <div className="h-full flex flex-col">
            {/* NEW: Execute button - horizontal layout with icon */}
            <div className="flex items-center gap-2 p-4 border-b">
                <Button 
                    variant="outline" 
                    size="icon" 
                    onClick={handleExecutePreviousNode}
                    title="Execute previous node"
                >
                    <Play className="h-4 w-4" />
                </Button>
                <span className="text-sm font-medium">
                    {selectedNode?.original?.label || "Execute Previous Node"}
                </span>
            </div>

            {/* NEW: Scope-based accordions (Variables & Artifacts scopes excluded) */}
            <div className="flex-1 overflow-auto">
                <Accordion type="multiple" className="w-full">
                    {scopedVariables.map((scope) => (
                        <AccordionItem key={scope.scopeId} value={scope.scopeId}>
                            <AccordionTrigger className="px-4 py-3 hover:bg-muted/50">
                                <div className="flex items-center gap-2">
                                    <span className="font-medium">{scope.label}</span>
                                    <span className="text-xs text-muted-foreground">
                                        ({scope.scopeType})
                                    </span>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent className="px-4 py-3 space-y-4">
                                {/* Display paths as {label: sample} */}
                                {Object.keys(scope.paths).length > 0 && (
                                    <div className="space-y-2">
                                        <h4 className="text-xs font-semibold text-muted-foreground uppercase">
                                            Fields
                                        </h4>
                                        <div className="space-y-1">
                                            {Object.entries(scope.paths).map(([label, data]: [string, any]) => (
                                                <div 
                                                    key={label} 
                                                    className="flex items-start justify-between gap-2 p-2 rounded hover:bg-muted/50"
                                                >
                                                    <div className="flex-1 min-w-0">
                                                        <div className="text-xs font-medium mb-1">
                                                            {label}
                                                        </div>
                                                        <code className="text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                                                            {data.displayPath}
                                                        </code>
                                                    </div>
                                                    <div className="text-xs text-muted-foreground truncate max-w-[120px]">
                                                        {JSON.stringify(data.sample)}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* Display artifacts if any exist */}
                                {scope.artifacts.length > 0 && (
                                    <div className="space-y-2">
                                        <h4 className="text-xs font-semibold text-muted-foreground uppercase">
                                            Artifacts
                                        </h4>
                                        <div className="space-y-1">
                                            {scope.artifacts.map((artifact: any, idx: number) => (
                                                <div 
                                                    key={idx} 
                                                    className="text-sm p-2 rounded bg-muted/50"
                                                >
                                                    {artifact.name || artifact.id || `Artifact ${idx + 1}`}
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* Show file data indicator if available */}
                                {scope.hasFileData && (
                                    <div className="text-xs text-muted-foreground">
                                        📄 File data available: {scope.fileId}
                                    </div>
                                )}
                            </AccordionContent>
                        </AccordionItem>
                    ))}
                </Accordion>
            </div>
        </div>
    )
}

export default NodeDetailInput
```

## Testing Checklist

### Unit Tests
- [ ] `scopedVariables` correctly filters out "variables" and "artifacts" scopes
- [ ] Paths transform to correct {label: sample} structure
- [ ] Empty paths array returns empty object
- [ ] Null/undefined response returns empty array

### Integration Tests
- [ ] `useNodeDetail` returns `scopedVariables` with correct structure
- [ ] Hook updates when `nodeVariableResponse` changes
- [ ] `refetchVariableInput` triggers re-fetch

### UI Tests
- [ ] Execute button displays with icon
- [ ] Node name shows next to execute button
- [ ] Accordions render for each scope
- [ ] Variables and Context accordions are NOT rendered
- [ ] Paths display with label and sample
- [ ] Artifacts list renders when present
- [ ] Loading state displays correctly

### E2E Tests
- [ ] Open node detail modal
- [ ] Verify scopes load (Run, Workflow, Input, Node scopes)
- [ ] Verify Variables and Artifacts scopes are hidden
- [ ] Click execute button → triggers action
- [ ] Expand/collapse accordions
- [ ] Copy display_path values work correctly

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Breaking change to existing consumers | HIGH | Update all components using `artifactInputs` and `variablesInputs` |
| Artifacts filtering logic unclear | MEDIUM | Clarify with backend team what makes artifact "invalid" |
| Performance with large scopes | LOW | Add virtualization if scope.paths > 100 items |
| Display_path formatting issues | LOW | Add truncation and tooltip for long paths |

## Rollback Plan

If issues occur:
1. Revert `use-node-detail.ts` changes
2. Restore `artifactInputs` and `variablesInputs` return values
3. Revert UI component changes
4. All consumers continue working with old structure

## Success Criteria

- [x] API response correctly processed into `scopedVariables`
- [x] Variables and Artifacts scopes filtered out
- [x] Paths displayed as {label: sample} format
- [x] Execute button redesigned with icon + node name
- [ ] No TypeScript errors
- [ ] All tests passing
- [ ] UI renders correctly in all states (loading, empty, populated)
- [ ] Performance acceptable with large datasets

## Notes

- Sample API response shows empty `artifacts: []` in all scopes
- Need to clarify artifact validity criteria with backend team
- Consider adding search/filter for large scope.paths arrays
- May need to add copy-to-clipboard for display_path values
