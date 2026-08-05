import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

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
import {
  BonusDetailsDrawer,
  Contracts,
  Purchases,
  UnitDetail,
} from "./App";


const emptyPurchases = {
  items: [],
  total: 0,
  page: 1,
  pages: 0,
};


describe("recoverable page loading", () => {
  it("shows the contracts load error and retries", async () => {
    const user = userEvent.setup();
    get.mockRejectedValueOnce(new Error("ERP indisponível")).mockResolvedValueOnce([]);

    render(
      <MemoryRouter>
        <Contracts />
      </MemoryRouter>,
    );

    expect(await screen.findByText("ERP indisponível")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Contratos por unidade")).toBeInTheDocument();
  });

  it("shows the unit load error and retries", async () => {
    const user = userEvent.setup();
    get.mockRejectedValueOnce(new Error("Unidade indisponível")).mockResolvedValueOnce({
      unit: { code: "054", display_name: "054 — Santa Maria", city: "Santa Maria", brand: "SHELL" },
      contract: null,
      series: [],
      bonus_rules: [],
    });

    render(
      <MemoryRouter initialEntries={["/unidades/054"]}>
        <Routes>
          <Route path="/unidades/:code" element={<UnitDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Unidade indisponível")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("054 — Santa Maria")).toBeInTheDocument();
  });

  it("loads the first purchases page only once", async () => {
    get.mockImplementation((path) => {
      if (path === "/units") return Promise.resolve([]);
      if (path.startsWith("/purchases?")) return Promise.resolve(emptyPurchases);
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });

    render(
      <MemoryRouter>
        <Purchases />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Compras e notas fiscais")).toBeInTheDocument();
    await waitFor(() => {
      const purchaseCalls = get.mock.calls.filter(([path]) => path.startsWith("/purchases?"));
      expect(purchaseCalls).toHaveLength(1);
    });
  });

  it("shows a purchases error and retries", async () => {
    const user = userEvent.setup();
    let purchasesFail = true;
    get.mockImplementation((path) => {
      if (path === "/units") return Promise.resolve([]);
      if (path.startsWith("/purchases?")) {
        return purchasesFail
          ? Promise.reject(new Error("Compras indisponíveis"))
          : Promise.resolve(emptyPurchases);
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });

    render(
      <MemoryRouter>
        <Purchases />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Compras indisponíveis")).toBeInTheDocument();
    purchasesFail = false;
    await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("Nenhum registro encontrado.")).toBeInTheDocument();
  });

  it("shows a bonus detail error and retries", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();

    render(
      <BonusDetailsDrawer
        data={null}
        loading={false}
        error="Composição indisponível"
        onRetry={onRetry}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText("Composição indisponível")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
