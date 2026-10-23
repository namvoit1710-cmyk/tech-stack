import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { StarButton } from "./star-button";

afterEach(() => cleanup());
const labels = { star: "Star", starred: "Starred", hint: "h", lockedHint: "locked" };

describe("StarButton (SA-2876)", () => {
    it("not starred: click calls onStar once", async () => {
        const onStar = rs.fn();
        render(<StarButton starred={false} count={2} onStar={onStar} labels={labels} />);
        await userEvent.click(screen.getByRole("button", { name: /Star/ }));
        expect(onStar).toHaveBeenCalledTimes(1);
        expect(screen.getByText("2")).toBeTruthy();
    });

    it("starred is permanent: button disabled, click does nothing", async () => {
        const onStar = rs.fn();
        render(<StarButton starred count={3} onStar={onStar} labels={labels} />);
        const btn = screen.getByRole("button", { name: /Starred/ });
        expect((btn as HTMLButtonElement).disabled).toBe(true);
        await userEvent.click(btn);
        expect(onStar).not.toHaveBeenCalled();
    });
});
