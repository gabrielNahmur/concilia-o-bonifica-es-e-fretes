import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

import { get, patch, remove } from "./api";
import { Administration } from "./App";


const adminUser = {
  id: "user-1",
  full_name: "Usuária QA",
  email: "user.qa@gbi.com",
  role: "viewer",
  active: true,
};


function renderAdministration() {
  return render(
    <MemoryRouter>
      <Administration />
    </MemoryRouter>,
  );
}


function mockResources(overrides = {}) {
  get.mockImplementation((path) => {
    if (path in overrides) {
      const value = overrides[path];
      return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
    }
    if (path === "/admin/users") return Promise.resolve([adminUser]);
    if (path === "/admin/freight-carriers") return Promise.resolve([]);
    if (path.startsWith("/portal-statements/")) return Promise.resolve({});
    return Promise.resolve([]);
  });
}


describe("administration resources and CRUD", () => {
  beforeEach(() => {
    patch.mockResolvedValue({});
    remove.mockResolvedValue({ ok: true });
  });

  it("keeps users available when the portal resource fails", async () => {
    const browserUser = userEvent.setup();
    mockResources({
      "/portal-statements/ipiranga/001": new Error("Portal indisponível"),
    });

    renderAdministration();

    expect(await screen.findByText("Usuária QA")).toBeInTheDocument();
    await browserUser.click(screen.getByRole("button", { name: "Portal Ipiranga" }));
    expect(await screen.findByText("Portal indisponível")).toBeInTheDocument();
    await browserUser.click(screen.getByRole("button", { name: "Usuários" }));
    expect(screen.getByText("Usuária QA")).toBeInTheDocument();
  });

  it("edits and deactivates a user without reloading unrelated resources", async () => {
    const browserUser = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockResources();

    renderAdministration();
    await screen.findByText("Usuária QA");
    await browserUser.click(screen.getByRole("button", { name: "Editar Usuária QA" }));
    const name = screen.getByRole("textbox", { name: "Nome" });
    await browserUser.clear(name);
    await browserUser.type(name, "Usuária Atualizada");
    await browserUser.click(screen.getByRole("button", { name: "Salvar usuário" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith(
      "/admin/users/user-1",
      expect.objectContaining({ full_name: "Usuária Atualizada" }),
    ));
    await browserUser.click(screen.getByRole("button", { name: "Inativar Usuária QA" }));
    expect(remove).toHaveBeenCalledWith("/admin/users/user-1");
    expect(get.mock.calls.some(([path]) => path.startsWith("/portal-statements/"))).toBe(false);
  });

  it("edits and removes a report recipient", async () => {
    const browserUser = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockResources({
      "/admin/recipients": [{ id: 7, name: "Diretoria QA", email: "diretoria.qa@gbi.com", active: true }],
    });

    renderAdministration();
    await browserUser.click(screen.getByRole("button", { name: "Destinatários" }));
    await screen.findByText("Diretoria QA");
    await browserUser.click(screen.getByRole("button", { name: "Editar Diretoria QA" }));
    await browserUser.click(screen.getByRole("button", { name: "Salvar destinatário" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(
      "/admin/recipients/7",
      expect.objectContaining({ email: "diretoria.qa@gbi.com" }),
    ));
    await browserUser.click(screen.getByRole("button", { name: "Remover Diretoria QA" }));
    expect(remove).toHaveBeenCalledWith("/admin/recipients/7");
  });

  it.each([
    ["Contratos", "/admin/contracts", "contracts", { id: 11, unit_code: "001", company_code: "IPIRANGA", start_date: "2026-01-01", end_date: "2027-01-01", term_months: 12, total_liters: 1000, upfront_total: 0, upfront_per_liter: 0, postpaid_per_liter: 0.08, umbrella_group: null, status: "active" }],
    ["Bonificações", "/admin/rules", "rules", { id: 12, unit_code: "001", company_code: "IPIRANGA", kind: "distributor_credit", effective_from: "2026-01-01", effective_to: null, rate_per_liter: 0.08, threshold_liters: null, milestone_liters: null, milestone_amount: null, period_months: null, due_day: null, due_month_offset: 1, applies_to: "all_fuel", active: true }],
    ["Fornecedores", "/admin/aliases", "aliases", { id: 13, unit_code: "001", company_code: "IPIRANGA", cnpj: "12345678000190", legal_name_pattern: "IPIRANGA", effective_from: "2026-01-01", effective_to: null, active: true }],
  ])("edits and deactivates %s", async (tabLabel, endpoint, type, row) => {
    const browserUser = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockResources({ [endpoint]: [row] });

    renderAdministration();
    await browserUser.click(screen.getByRole("button", { name: tabLabel }));
    await browserUser.click(await screen.findByRole("button", { name: `Editar ${type} ${row.id}` }));
    await browserUser.click(screen.getByRole("button", { name: "Salvar" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(`${endpoint}/${row.id}`, expect.any(Object)));
    await browserUser.click(screen.getByRole("button", { name: `Inativar ${type} ${row.id}` }));
    expect(remove).toHaveBeenCalledWith(`${endpoint}/${row.id}`);
  });
});
