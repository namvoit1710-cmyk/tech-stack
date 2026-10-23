import { ClassicPreset } from "rete";

export class ObjectControl extends ClassicPreset.Control {
    constructor(public payload: Record<string, any>) {
        super();
    }
}