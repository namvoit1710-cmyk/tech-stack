import { useState } from "react";

const useRowSelection = () => {
    const [rowSelections, setRowSelections] = useState<Record<string, boolean>>({});
    
    const hasRowSelected = Object.values(rowSelections).some(selected => selected);

    return {
        rowSelections,
        setRowSelections,
        hasRowSelected,
    }
}

export default useRowSelection