import { render, screen } from "@testing-library/react";
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
import { MonthlyRoutine } from "./App";


const routinePayload = {
  cards: [{
    id: "003:cumulative:2026-06-01",
    unit_code: "003",
    unit_name: "Bagé",
    brand: "IPIRANGA",
    company_code: "IPIRANGA",
    rule_label: "Crédito na distribuidora",
    source_type: "ipiranga_portal",
    source: { label: "Extrato Ipiranga", formats: [".pdf"] },
    situation: "automatic",
    confirmation_mode: "automatic",
    cumulative: true,
    as_of_date: "2026-06-30",
    expected_value: 300,
    observed_value: 200,
    difference_value: 0,
    portal_credit_total_value: 250,
    portal_unallocated_value: 50,
    portal_difference_value: 100,
    historical_adjustment_value: 100,
    adjustment_note: "Ajuste histórico aprovado",
    next_statement_expected_value: 0,
    credit_event_count: 2,
    latest_import: {
      original_filename: "portal-008-20260724.xlsx",
      created_at: "2026-07-24T15:00:00Z",
      latest_credit_date: "2026-06-28",
      imported_count: 48,
    },
    imports: [],
    competencies: [],
    credit_history: [],
    queue_url: "/conciliacoes?unit=003&state=confirmed",
    action: "Conciliação acumulada concluída",
    description: "Os créditos apropriados fecham o total contratual.",
  }],
  summary: { automatic: 1, analysis: 0, open_value: 0 },
  source_options: [],
  imports: [],
  raizen_receipts: [],
};


describe("monthly routine cumulative card", () => {
  beforeEach(() => {
    get.mockImplementation((path) => {
      if (path === "/units") return Promise.resolve([]);
      if (path.startsWith("/monthly-routine?")) return Promise.resolve(routinePayload);
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
  });

  it("separates the residual wallet balance from the credits appropriated to competencies", async () => {
    render(<MemoryRouter><MonthlyRoutine user={{ id: "viewer", role: "viewer" }} /></MemoryRouter>);

    expect(await screen.findByText("Créditos apropriados")).toBeInTheDocument();
    expect(screen.getByText("R$ 200,00")).toBeInTheDocument();
    expect(screen.getByText(/Crédito residual no portal: R\$ 50,00/)).toBeInTheDocument();
    expect(screen.getByText(/Créditos brutos emitidos: R\$ 250,00/)).toBeInTheDocument();
  });

  it("shows the import date and the last issued credit instead of a future contract schedule", async () => {
    render(<MemoryRouter><MonthlyRoutine user={{ id: "viewer", role: "viewer" }} /></MemoryRouter>);

    expect(await screen.findByText(/Último extrato: portal-008-20260724\.xlsx/)).toBeInTheDocument();
    expect(screen.getByText(/Importado em 24\/07\/2026/)).toBeInTheDocument();
    expect(screen.getByText(/Créditos emitidos até 28\/06\/2026/)).toBeInTheDocument();
    expect(screen.queryByText(/26\/06\/2027/)).not.toBeInTheDocument();
  });
});
