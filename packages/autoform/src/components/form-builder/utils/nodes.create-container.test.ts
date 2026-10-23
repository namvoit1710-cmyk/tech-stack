import { describe, expect, it } from "@rstest/core";
import { WRAPPER_DEFS } from "../constants/palette";
import { createContainer } from "./nodes";

const odataDef = WRAPPER_DEFS.find((d) => d.wrapperType === "OdataWrapper")!;

describe("createContainer — OdataWrapper", () => {
  it("does not seed isShowRowMoreAction: nothing reads it", () => {
    const wp = createContainer(odataDef).fieldConfig.wrapperProps ?? {};
    expect(wp).not.toHaveProperty("isShowRowMoreAction");
  });

  it("does not seed endpoint: the source is resolved from __<key>_file_id at runtime", () => {
    const wp = createContainer(odataDef).fieldConfig.wrapperProps ?? {};
    expect(wp).not.toHaveProperty("endpoint");
  });

  it("still seeds the five capability flags the wrapper does read", () => {
    const wp = createContainer(odataDef).fieldConfig.wrapperProps ?? {};
    expect(wp).toMatchObject({
      isAdd: true,
      isEdit: true,
      isDelete: true,
      isShowColumnSetting: true,
      isShowSearch: true,
    });
  });
});

const tabDef = WRAPPER_DEFS.find((d) => d.wrapperType === "TabWrapper")!;

describe("createContainer — TabWrapper", () => {
  it("seeds variant and orientation explicitly, so the JSON says what the tabs look like", () => {
    const wp = createContainer(tabDef).fieldConfig.wrapperProps ?? {};
    expect(wp).toMatchObject({ variant: "default", orientation: "horizontal" });
  });

  it("still seeds the two default slots", () => {
    expect(createContainer(tabDef).slots).toHaveLength(2);
  });
});
