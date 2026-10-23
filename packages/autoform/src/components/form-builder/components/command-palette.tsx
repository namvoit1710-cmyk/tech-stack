import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@ldc/ui/components/command";
import { Popover, PopoverAnchor, PopoverContent } from "@ldc/ui/components/popover";

import { FIELD_CATEGORIES, FIELD_DEFS, WRAPPER_DEFS } from "../constants/palette";
import { useDesignerCtx } from "../hooks/use-designer-context";
import { findNodeById } from "../utils/nodes";

export interface CommandPaletteState {
  open: boolean;
  point: { x: number; y: number } | null;
  targetContainerId: string | null;
  targetSlotName?: string;
  targetSlotId?: string;
}

export const CLOSED_PALETTE: CommandPaletteState = {
  open: false,
  point: null,
  targetContainerId: null,
};

interface CommandPaletteProps {
  state: CommandPaletteState;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ state, onOpenChange }: CommandPaletteProps) {
  const { nodes, addField, addContainer, addContainerToSlot, addFieldToOdataSlot } = useDesignerCtx();
  const { point, targetContainerId, targetSlotName, targetSlotId } = state;

  const close = () => onOpenChange(false);

  const handleSelectField = (type: string) => {
    const def = FIELD_DEFS.find((d) => d.type === type);
    if (!def) return;
    // OdataWrapper slot-targeted selections go to container.children with slot set;
    // other selections route to existing addField (columns or root)
    if (targetContainerId && targetSlotName) {
      addFieldToOdataSlot(def, targetContainerId, targetSlotName);
    } else {
      addField(def, targetContainerId, -1, targetSlotId);
    }
    close();
  };

  const handleSelectWrapper = (wrapperType: string) => {
    const def = WRAPPER_DEFS.find((d) => d.wrapperType === wrapperType);
    if (!def) return;
    const target = targetContainerId ? findNodeById(nodes, targetContainerId)?.node : null;
    const slot = target?._kind === "container"
      ? (targetSlotId ? target.slots?.find((s) => s._id === targetSlotId) : target.slots?.[0])
      : undefined;
    if (target?._kind === "container" && slot) {
      addContainerToSlot(def, target._id, slot._id, -1);
    } else {
      addContainer(def, -1);
    }
    close();
  };

  return (
    <Popover open={state.open} onOpenChange={onOpenChange}>
      <PopoverAnchor asChild>
        <span
          style={{
            position: "fixed",
            left: point?.x ?? 0,
            top: point?.y ?? 0,
            width: 0,
            height: 0,
          }}
        />
      </PopoverAnchor>
      <PopoverContent
        className="w-[210px] p-1.5 rounded-lg shadow-lg"
        align="start"
        onWheel={(e) => e.stopPropagation()}
      >
        <Command>
          <CommandInput placeholder="Search controls..." autoFocus />
          <CommandList className="max-h-72 overflow-y-auto">
            <CommandEmpty>No controls found.</CommandEmpty>
            <CommandGroup heading="Wrappers">
              {WRAPPER_DEFS.map((def) => (
                <CommandItem
                  key={def.wrapperType}
                  value={def.label}
                  onSelect={() => handleSelectWrapper(def.wrapperType ?? "")}
                >
                  {def.label}
                </CommandItem>
              ))}
            </CommandGroup>
            {FIELD_CATEGORIES.map((category) => {
              const defs = FIELD_DEFS.filter((d) => d.category === category);
              if (defs.length === 0) return null;
              return (
                <CommandGroup key={category} heading={category}>
                  {defs.map((def) => (
                    <CommandItem
                      key={def.type}
                      value={def.label}
                      onSelect={() => handleSelectField(def.type)}
                    >
                      {def.label}
                    </CommandItem>
                  ))}
                </CommandGroup>
              );
            })}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
