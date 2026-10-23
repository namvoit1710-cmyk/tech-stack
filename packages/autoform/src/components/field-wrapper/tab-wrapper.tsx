import { cn } from "@ldc/ui";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@ldc/ui/components/tabs";
import type { FieldWrapperProps, IField } from "../../types/schema";

const TabItemWrapper = (props: FieldWrapperProps) => {
    const { children, field } = props;

    if (!field) return <>{children}</>;

    const className = (field.fieldConfig.wrapperProps?.className as string | undefined) ?? "";

    return (
        <TabsContent
            value={field.key}
            className={cn("flex-2 px-3", className)}
        >
            {children}
        </TabsContent>
    );
};

export interface ITabWrapperProps {
    children: React.ReactNode;
    field?: IField;
}

const TabWrapper = (props: ITabWrapperProps) => {
    const { children, field } = props;
    const tabFields = (field?.fields ?? []).filter(
        (f) => f.fieldConfig.fieldWrapper === "TabItemWrapper"
    );
    const variant = field?.fieldConfig.wrapperProps?.variant ?? "default";
    const defaultValue = tabFields.length > 0 ? tabFields[0]?.key : undefined;

    return (
        <Tabs
            defaultValue={defaultValue}
            className="w-full flex flex-col"
            orientation={(field?.fieldConfig.wrapperProps?.orientation as "horizontal" | "vertical") ?? "horizontal"}
        >
            <TabsList variant={variant}>
                {tabFields.map((tabField) => {
                    const label: string =
                        tabField.fieldConfig.wrapperProps?.label ?? "";
                    const value = tabField.key;
                    return (
                        <TabsTrigger key={value} value={value} className="hover:cursor-pointer">
                            {label}
                        </TabsTrigger>
                    );
                })}
            </TabsList>

            {children}
        </Tabs>
    );
};

TabWrapper.displayName = "TabWrapper";

export { TabItemWrapper, TabWrapper };

