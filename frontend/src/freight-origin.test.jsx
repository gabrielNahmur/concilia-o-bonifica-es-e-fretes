import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

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

import { FreightOriginFacts, FreightOriginSummary } from "./App";

it("shows the registered city in the CT-e origin facts", () => {
  render(
    <FreightOriginFacts
      senderName="IPIRANGA PRODUTOS DE PETROLEO"
      senderCnpj="33337122015906"
      origins={[{ cnpj: "33337122015906", city: "Canoas", state: "RS" }]}
    />,
  );

  expect(screen.getByText("Origem cadastral: Canoas/RS")).toBeInTheDocument();
});

it("labels the city as registered origin rather than loading proof", () => {
  render(<FreightOriginSummary origin={{ cnpj: "33453598013705", city: "Esteio", state: "RS" }} />);

  expect(screen.getByText("Origem cadastral: Esteio/RS")).toBeInTheDocument();
  expect(screen.queryByText(/base comprovada/i)).not.toBeInTheDocument();
});

it("does not invent an origin and reports a missing registered city", () => {
  const { rerender } = render(<FreightOriginSummary origin={{ cnpj: "33453598013705" }} />);

  expect(screen.getByText("Cidade cadastral não identificada")).toBeInTheDocument();

  rerender(<FreightOriginSummary origin={null} />);

  expect(screen.queryByText(/Origem cadastral|Cidade cadastral/)).not.toBeInTheDocument();
});
