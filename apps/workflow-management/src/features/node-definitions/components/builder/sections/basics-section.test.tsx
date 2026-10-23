import { afterEach, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

// Stub i18n so t() returns the key string.
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }));

import BasicsSection from "./basics-section";
import type { IBasicsValue } from "./basics-section";

const base: IBasicsValue = {
    name: "", description: "", baseWorkerId: "", icon: "",
    color: "", tags: [], kind: "action", collection: "My Nodes",
};

afterEach(() => { void cleanup(); });

test("renders Node kind and Collection fields", () => {
    render(<BasicsSection value={base} onChange={rs.fn()} workerOptions={[]} />);
    expect(screen.getByText(/node_definition_builder\.info_kind/i)).toBeTruthy();
    expect(screen.getByText(/node_definition_builder\.info_collection/i)).toBeTruthy();
});
