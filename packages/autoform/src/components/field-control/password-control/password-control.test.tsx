/// <reference types="@testing-library/jest-dom" />

import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PasswordControl from "./password-control";

describe("PasswordControl", () => {
    it("renders an input with type=password by default", () => {
        render(<PasswordControl name="pwd" aria-label="Password" />);
        const inputs = screen.getAllByLabelText("Password");
        const input = inputs[inputs.length - 1];
        expect(input).toHaveAttribute("type", "password");
    });

    it("shows a toggle button with aria-label 'Show password'", () => {
        render(<PasswordControl name="pwd" aria-label="Password" />);
        const buttons = screen.getAllByRole("button", { name: "Show password" });
        expect(buttons[buttons.length - 1]).toBeInTheDocument();
    });

    it("switches input type to text when toggle is clicked", async () => {
        const user = userEvent.setup();
        render(<PasswordControl name="pwd" aria-label="Password" />);
        const button = screen.getAllByRole("button", { name: "Show password" }).at(-1)!;
        await user.click(button);
        const input = screen.getAllByLabelText("Password").at(-1)!;
        expect(input).toHaveAttribute("type", "text");
    });

    it("switches aria-label to 'Hide password' after toggle", async () => {
        const user = userEvent.setup();
        render(<PasswordControl name="pwd" aria-label="Password" />);
        const showButton = screen.getAllByRole("button", { name: "Show password" }).at(-1)!;
        await user.click(showButton);
        const hideButton = screen.getAllByRole("button", { name: "Hide password" }).at(-1)!;
        expect(hideButton).toBeInTheDocument();
    });

    it("switches back to type=password when toggled again", async () => {
        const user = userEvent.setup();
        render(<PasswordControl name="pwd" aria-label="Password" />);
        const showButton = screen.getAllByRole("button", { name: "Show password" }).at(-1)!;
        await user.click(showButton);
        const hideButton = screen.getAllByRole("button", { name: "Hide password" }).at(-1)!;
        await user.click(hideButton);
        const input = screen.getAllByLabelText("Password").at(-1)!;
        expect(input).toHaveAttribute("type", "password");
    });

    it("sets aria-invalid on the input when error is truthy", () => {
        render(<PasswordControl name="pwd" aria-label="Password" error={{ message: "Required" }} />);
        const inputs = screen.getAllByLabelText("Password");
        const input = inputs[inputs.length - 1];
        expect(input).toHaveAttribute("aria-invalid", "true");
    });

    it("sets aria-invalid on the input when error is a boolean true", () => {
        render(<PasswordControl name="pwd" aria-label="Password" error={true} />);
        const inputs = screen.getAllByLabelText("Password");
        const input = inputs[inputs.length - 1];
        expect(input).toHaveAttribute("aria-invalid", "true");
    });

    it("disables both input and toggle when disabled=true", () => {
        render(<PasswordControl name="pwd" aria-label="Password" disabled />);
        const inputs = screen.getAllByLabelText("Password");
        const input = inputs[inputs.length - 1];
        expect(input).toBeDisabled();
        const buttons = screen.getAllByRole("button", { name: "Show password" });
        expect(buttons[buttons.length - 1]).toBeDisabled();
    });

    it("merges a passed className with the built-in pr-10 (does not clobber it)", () => {
        render(<PasswordControl name="pwd" aria-label="Password" className="text-right font-mono" />);
        const input = screen.getAllByLabelText("Password").at(-1)!;
        expect(input).toHaveClass("pr-10");
        expect(input).toHaveClass("text-right");
        expect(input).toHaveClass("font-mono");
    });
});
