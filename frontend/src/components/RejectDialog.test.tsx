import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { RejectDialog } from "./RejectDialog";

describe("RejectDialog", () => {
  it("cannot reject without a reason", async () => {
    const reasons: string[] = [];
    render(<RejectDialog onConfirm={(r) => reasons.push(r)} onCancel={() => undefined} />);
    const reject = screen.getByRole("button", { name: "Reject" });
    expect(reject).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Reason for rejection"), "   ");
    expect(reject).toBeDisabled();
    await userEvent.click(reject);
    expect(reasons).toEqual([]);
  });

  it("submits the trimmed reason", async () => {
    const reasons: string[] = [];
    render(<RejectDialog onConfirm={(r) => reasons.push(r)} onCancel={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Reason for rejection"), "  Missed the truck ");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(reasons).toEqual(["Missed the truck"]);
  });

  it("can be cancelled with Escape", async () => {
    let cancelled = 0;
    render(
      <RejectDialog
        onConfirm={() => undefined}
        onCancel={() => {
          cancelled += 1;
        }}
      />,
    );
    await userEvent.type(screen.getByLabelText("Reason for rejection"), "{Escape}");
    expect(cancelled).toBe(1);
  });
});
