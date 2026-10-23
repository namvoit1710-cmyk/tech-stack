import { Button } from "@ldc/ui/components/button";
import { Minus } from "lucide-react";
import { memo } from "react";

interface ActionCellProps {
    rowId: string;
    onRemove: (id: string) => void;
}

const ActionCell = memo(({ rowId, onRemove }: ActionCellProps) => (
    <Button
        size="icon"
        variant="destructive"
        onClick={() => onRemove(rowId)}
    >
        <Minus />
    </Button>
));

ActionCell.displayName = "ActionCell";

export default ActionCell;
