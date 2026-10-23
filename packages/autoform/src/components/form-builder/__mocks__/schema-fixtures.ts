import type { DesignerContainer, DesignerField } from "../types";
import { uid } from "../utils/nodes";

/**
 * Create an OdataWrapper with slot fields (filter button, more-action button).
 * Returns a container with columns and button children with slot properties.
 */
export function createOdataWithSlotFields(): DesignerContainer {
  const rowField: DesignerField = {
    _id: uid(),
    _kind: "field",
    key: "user_name",
    outputType: "string",
    fieldConfig: {
      fieldWrapper: "FormItemWrapper",
      fieldControl: "InputControl",
      wrapperProps: { label: "User Name" },
      controlProps: { placeholder: "Enter name" },
      rules: [],
      condition: null,
    },
  };

  const filterButtonField: DesignerField = {
    _id: uid(),
    _kind: "field",
    key: "filter_btn",
    outputType: "string",
    fieldConfig: {
      slot: "filter",
      fieldWrapper: "FormItemWrapper",
      fieldControl: "ButtonControl",
      wrapperProps: { label: "Filter" },
      controlProps: { label: "Filter", variant: "outline" },
      rules: [],
      condition: null,
    },
  };

  const moreActionButtonField: DesignerField = {
    _id: uid(),
    _kind: "field",
    key: "more_action_btn",
    outputType: "string",
    fieldConfig: {
      slot: "more-action",
      fieldWrapper: "FormItemWrapper",
      fieldControl: "ButtonControl",
      wrapperProps: { label: "More Actions" },
      controlProps: { label: "More...", variant: "ghost" },
      rules: [],
      condition: null,
    },
  };

  return {
    _id: uid(),
    _kind: "container",
    wrapperType: "OdataWrapper",
    key: "odata_table_1",
    outputType: "array",
    fieldConfig: {
      fieldWrapper: "OdataWrapper",
      wrapperProps: { label: "User Table" },
      controlProps: {},
      rules: [],
      condition: null,
    },
    children: [filterButtonField, moreActionButtonField],
    columns: [rowField],
  };
}
