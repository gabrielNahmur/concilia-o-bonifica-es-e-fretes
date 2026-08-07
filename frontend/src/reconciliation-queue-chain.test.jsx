import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QueueChain } from "./App";


describe("QueueChain", () => {
  it("mostra o desconto lançado no ERP mesmo quando a baixa foi registrada em atraso", () => {
    render(
      <QueueChain
        row={{
          document: "3096076",
          item_type: "invoice",
          rule_kind: "invoice_discount",
          status: "late_payment",
          expected_value: 0,
          observed_value: 0,
          identified_discount_value: 920,
        }}
        activeItem={{
          item_type: "invoice",
          status: "late_payment",
          expected_value: 0,
          observed_value: 0,
          details: { raw_discount_value: 920 },
        }}
        detail={{
          reference_month: "2026-08-01",
          expected_value: 0,
          observed_value: 0,
          manual_adjustment: 0,
          evidence: [],
          chains: [
            {
              purchase: { invoice_number: "3096076", expected_bonus: 920 },
              actual_discount: 0,
              documents: [],
            },
          ],
          match_summary: { purchase_count: 1 },
          rule: { formula: "R$ 0,040000/L" },
        }}
      />,
    );

    expect(screen.getByText("4. Desconto identificado no ERP")).toBeInTheDocument();
    expect(screen.getByText("Não apropriado por atraso na baixa.")).toBeInTheDocument();
    expect(screen.getByText("R$ 920,00")).toBeInTheDocument();
  });

  it("descreve uso de crédito da 004 sem apresentar o esperado mensal como prova", () => {
    render(
      <QueueChain
        row={{
          document: "2983393",
          item_type: "portal_credit_usage",
          rule_kind: "distributor_credit",
          expected_value: 8750,
          observed_value: 8750,
        }}
        activeItem={{
          item_type: "portal_credit_usage",
          expected_value: 8750,
          observed_value: 8750,
          display_evidence: [
            {
              source: "IPIRANGA_PORTAL_USAGE",
              label: "Crédito postecipado usado no portal Ipiranga",
              date: "2025-11-18",
              document: "2983393",
              value: 8750,
            },
          ],
        }}
        detail={{
          reference_month: "2025-11-01",
          expected_value: 9100,
          observed_value: 0,
          manual_adjustment: 0,
          evidence: [],
          chains: [],
          match_summary: { purchase_count: 13 },
          rule: { formula: "R$ 0,070000/L" },
        }}
      />,
    );

    expect(screen.getByText(/NF utilizada pelo portal/)).toBeInTheDocument();
    expect(screen.getByText(/Crédito confirmado/)).toBeInTheDocument();
    expect(screen.getAllByText(/8\.750,00/)).toHaveLength(2);
    expect(screen.queryByText("Esperado R$ 9.100,00")).not.toBeInTheDocument();
  });
});
