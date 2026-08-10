import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  get: vi.fn(),
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

import { get } from "./api";
import { Reconciliations } from "./App";


const user = { id: "viewer", full_name: "Pessoa QA", role: "viewer" };
const queuePayload = {
  items: [],
  total: 0,
  page: 1,
  pages: 0,
  summary: {
    to_treat: 0,
    value_at_risk: 0,
    in_review: 0,
    waiting: 0,
    confirmed: 0,
  },
};
const exceptionPayload = {
  items: [],
  total: 0,
  page: 1,
  pages: 0,
  summary: {
    open: 0,
    critical: 0,
    high: 0,
    in_review: 0,
    value_at_risk: 0,
    types: {},
  },
};
const competencePayload = {
  items: [],
  total: 0,
  page: 1,
  pages: 0,
  summary: {
    expected_value: 0,
    observed_value: 0,
    difference_value: 0,
    status_counts: {},
    review_required: 0,
    manually_reviewed: 0,
    automatically_confirmed: 0,
  },
};
const coveragePayload = {
  summary: {
    total_items: 0,
    exact_confirmed: 0,
    settled_exceptions: 0,
    waiting_payment: 0,
    closed_rate: 0,
    automatic_integrity_rate: 100,
    automatic_integrity_violations: 0,
    expected_values: {},
    counts: {},
    labels: {},
  },
  policy: {
    name: "Política estrita",
    positive_confirmation: "Confirma somente cadeia exata.",
    negative_classification: "Classifica exceções comprovadas.",
  },
  units: [],
};


function mockSuccessfulPanels() {
  get.mockImplementation((path) => {
    if (path === "/units") return Promise.resolve([]);
    if (path.startsWith("/reconciliations/work-queue?")) return Promise.resolve(queuePayload);
    if (path.startsWith("/reconciliations/exceptions?")) return Promise.resolve(exceptionPayload);
    if (path === "/reconciliations/coverage") return Promise.resolve(coveragePayload);
    if (path.startsWith("/reconciliations?")) return Promise.resolve(competencePayload);
    return Promise.reject(new Error(`Unexpected request: ${path}`));
  });
}


describe("reconciliation queue navigation", () => {
  beforeEach(() => {
    mockSuccessfulPanels();
  });

  it("opens the queue directly without the additional view selector", async () => {
    render(
      <MemoryRouter initialEntries={["/conciliacoes"]}>
        <Reconciliations user={user} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Fila de conciliação" })).toBeInTheDocument();
    expect(await screen.findByText("Itens a tratar")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fila", exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Exceções", exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Competências", exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cobertura automática", exact: true })).not.toBeInTheDocument();
  });

  it("exposes detailed financial situations and loads the operational default set", async () => {
    render(
      <MemoryRouter initialEntries={["/conciliacoes"]}>
        <Reconciliations user={user} />
      </MemoryRouter>,
    );

    await screen.findByRole("heading");
    const situationTrigger = screen.getByText(/Situa/).parentElement.querySelector("button");
    fireEvent.click(situationTrigger);

    expect(screen.getByText("Pago maior")).toBeInTheDocument();
    expect(screen.getByText("Pago menor")).toBeInTheDocument();
    expect(screen.getByText("Pago em atraso")).toBeInTheDocument();
    expect(screen.getByText("Ajuste aprovado")).toBeInTheDocument();
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining("state=pending")));
    expect(get).toHaveBeenCalledWith(expect.stringContaining("state=overpaid"));
    expect(get).toHaveBeenCalledWith(expect.stringContaining("state=underpaid"));
    expect(get).toHaveBeenCalledWith(expect.stringContaining("state=overdue"));
    expect(get).toHaveBeenCalledWith(expect.stringContaining("state=in_review"));
  });
});
