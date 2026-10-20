import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LabelPrompt } from "./LabelPrompt";

const anchor = { x: 0.1, y: 0.1, w: 0.2, h: 0.2 };

describe("LabelPrompt", () => {
  it("confirms a cleaned label on Enter and offers existing labels as suggestions", async () => {
    const labels: string[] = [];
    const { container } = render(
      <LabelPrompt
        anchor={anchor}
        suggestions={["Car", "Traffic Light"]}
        onConfirm={(l) => labels.push(l)}
        onCancel={() => undefined}
      />,
    );
    expect(container.querySelectorAll("datalist option")).toHaveLength(2);
    await userEvent.type(screen.getByLabelText("Entity name"), "  Fire   hydrant {Enter}");
    expect(labels).toEqual(["Fire hydrant"]);
  });

  it("does not confirm blank names and cancels on Escape", async () => {
    const labels: string[] = [];
    let cancelled = 0;
    render(
      <LabelPrompt
        anchor={anchor}
        suggestions={[]}
        onConfirm={(l) => labels.push(l)}
        onCancel={() => {
          cancelled += 1;
        }}
      />,
    );
    await userEvent.type(screen.getByLabelText("Entity name"), "   {Enter}");
    expect(labels).toEqual([]);
    await userEvent.type(screen.getByLabelText("Entity name"), "{Escape}");
    expect(cancelled).toBe(1);
  });
});
