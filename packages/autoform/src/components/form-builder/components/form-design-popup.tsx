import { Dialog, DialogContent, DialogTrigger } from "@ldc/ui/components/dialog";
import type { IField } from "../../../types/schema";
import FormBuilder from "./form-builder";

const FormDesignPopup = ({ children, value, onChange }: { children: React.ReactNode, value: IField[], onChange: (value: IField[]) => void }) => {
    return (
        <Dialog>
            <DialogTrigger asChild>
                {children}
            </DialogTrigger>

            <DialogContent className="min-w-[90vw] h-[90vh]">
                <FormBuilder
                    value={{ fields: value }}
                    onChange={(schema) => onChange(schema.fields)}
                />
            </DialogContent>
        </Dialog>
    )
}

export default FormDesignPopup;