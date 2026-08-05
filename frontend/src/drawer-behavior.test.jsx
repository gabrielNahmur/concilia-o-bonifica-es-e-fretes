import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  get: vi.fn(() => new Promise(() => {})),
  patch: vi.fn(),
  post: vi.fn(),
  remove: vi.fn(),
  upload: vi.fn(),
}));

vi.mock("recharts", () => {
  const Container = ({ children }) => <div>{children}</div>;
  const Element = () => null;
  return {
    Area: Element,
    AreaChart: Container,
    Bar: Element,
    BarChart: Container,
    CartesianGrid: Element,
    Cell: Element,
    ComposedChart: Container,
    Legend: Element,
    Line: Element,
    ResponsiveContainer: Container,
    Tooltip: Element,
    XAxis: Element,
    YAxis: Element,
  };
});

import { PurchaseDetail } from "./App";

beforeEach(() => {
  document.body.style.overflow = "auto";
});

afterEach(() => {
  document.body.style.overflow = "";
});

it("trava o fundo, fecha com Escape e restaura a rolagem ao desmontar", async () => {
  const onClose = vi.fn();
  const view = render(<PurchaseDetail entryId="NF-1" onClose={onClose} />);

  await waitFor(() => expect(document.body.style.overflow).toBe("hidden"));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();

  view.unmount();
  expect(document.body.style.overflow).toBe("auto");
});
