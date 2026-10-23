import { createContext, useContext } from "react";
import BuilderField from "../components/builder/builder-field";
import type { IField } from "../types/schema";

export interface SlotEntry {
    field: IField;
    path: string[];
}

export interface SlotRegistry {
    slots: Record<string, SlotEntry[]>;
}

const SlotContext = createContext<SlotRegistry>({ slots: {} });

export const SlotProvider = SlotContext.Provider;

export const useSlots = () => useContext(SlotContext);

export const Slot = ({ name, ...extraProps }: { name: string;[key: string]: any }) => {
    const { slots } = useSlots();
    const entries = slots[name];

    if (!entries?.length) return null;

    return (
        <>
            {entries.map((entry) => (
                <BuilderField
                    key={entry.path.join(".")}
                    field={{
                        ...entry.field,
                        fieldConfig: {
                            ...entry.field.fieldConfig,
                            controlProps: {
                                ...entry.field.fieldConfig.controlProps,
                                ...extraProps,
                            },
                        },
                    }}
                    path={entry.path}
                />
            ))}
        </>
    );
};