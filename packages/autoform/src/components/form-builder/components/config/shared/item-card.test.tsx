/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ItemCard, ItemList } from "./item-card";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

describe("ItemCard", () => {
    it("renders short header fields inline and wide fields full-width below", () => {
        render(
            <ItemCard header={<input placeholder="key" />}>
                <input placeholder="value" />
            </ItemCard>
        );
        expect(screen.getByPlaceholderText("key")).toBeInTheDocument();
        expect(screen.getByPlaceholderText("value")).toBeInTheDocument();
    });

    it("renders a remove button that fires onRemove", async () => {
        const onRemove = rs.fn();
        const user = userEvent.setup();
        render(<ItemCard onRemove={onRemove} removeLabel="Remove thing" />);

        await user.click(screen.getByRole("button", { name: "Remove thing" }));
        expect(onRemove).toHaveBeenCalled();
    });

    it("renders no header row at all when neither header nor onRemove is given", () => {
        render(<ItemCard>only children</ItemCard>);
        expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });
});

describe("ItemList", () => {
    it("shows the empty state when there are no items", () => {
        render(<ItemList isEmpty emptyLabel="No rows yet" addLabel="Add" onAdd={rs.fn()} />);
        expect(screen.getByText("No rows yet")).toBeInTheDocument();
    });

    it("shows children instead of the empty state when not empty", () => {
        render(
            <ItemList isEmpty={false} addLabel="Add" onAdd={rs.fn()}>
                <span>a row</span>
            </ItemList>
        );
        expect(screen.getByText("a row")).toBeInTheDocument();
    });

    it("renders a full-width Add button that fires onAdd", async () => {
        const onAdd = rs.fn();
        const user = userEvent.setup();
        render(<ItemList isEmpty addLabel="+ Add wrapper prop" onAdd={onAdd} />);

        await user.click(screen.getByRole("button", { name: "+ Add wrapper prop" }));
        expect(onAdd).toHaveBeenCalled();
    });
});
