import assert from "node:assert/strict";
import test from "node:test";

import {
  informationRequests,
  reconciliationAuditActionLabel,
  reviewActionAvailability,
} from "./reconciliation-actions.js";

test("does not offer item-level review actions without a reconciliation item", () => {
  assert.deepEqual(reviewActionAvailability(null), {
    canAcceptLink: false,
    canRequestInformation: false,
    canRejectLink: false,
    unavailableMessage: "Não há vínculo individual para revisar nesta competência.",
  });
});

test("offers request/reject for a linked item and accepts only an exact link", () => {
  assert.deepEqual(reviewActionAvailability({ id: "item-1", difference_value: 0 }), {
    canAcceptLink: true,
    canRequestInformation: true,
    canRejectLink: true,
    unavailableMessage: "",
  });

  assert.deepEqual(reviewActionAvailability({ id: "item-2", difference_value: 10 }), {
    canAcceptLink: false,
    canRequestInformation: true,
    canRejectLink: true,
    unavailableMessage: "",
  });
});

test("turns internal information requests into operational drawer data", () => {
  assert.deepEqual(
    informationRequests([
      {
        id: "item-1",
        source_document: "2958247",
        review_status: "needs_information",
        reviewed_by: "Gustavo Barrera",
        reviewed_at: "2026-08-05T13:00:00Z",
        review_notes: "[boleto_pendente] Solicitar boleto e comprovante ao financeiro.",
      },
      {
        id: "item-2",
        source_document: "2957262",
        review_status: "pending",
        review_notes: "[ignorar] Não deve ser exibido.",
      },
    ]),
    [
      {
        id: "legacy:item-1",
        itemId: "item-1",
        document: "2958247",
        status: "open",
        open: true,
        requestedBy: "Gustavo Barrera",
        requestedAt: "2026-08-05T13:00:00Z",
        reason: "boleto pendente",
        notes: "Solicitar boleto e comprovante ao financeiro.",
      },
    ],
  );
});

test("keeps an answered request visible with its response and closure history", () => {
  assert.deepEqual(
    informationRequests([
      {
        id: "request-1",
        item_id: "item-1",
        document: "2958247",
        status: "closed",
        reason_code: "boleto_pendente",
        request_notes: "Confirmar se o desconto foi aplicado.",
        requested_by: "Gestor Solicitante",
        requested_at: "2026-08-05T13:00:00Z",
        response: "Financeiro confirmou que não houve desconto.",
        responded_by: "Gestora Respondente",
        responded_at: "2026-08-05T14:00:00Z",
        closed_by: "Gestora Respondente",
        closed_at: "2026-08-05T14:00:00Z",
      },
    ]),
    [
      {
        id: "request-1",
        itemId: "item-1",
        document: "2958247",
        status: "closed",
        open: false,
        reason: "boleto pendente",
        notes: "Confirmar se o desconto foi aplicado.",
        requestedBy: "Gestor Solicitante",
        requestedAt: "2026-08-05T13:00:00Z",
        response: "Financeiro confirmou que não houve desconto.",
        respondedBy: "Gestora Respondente",
        respondedAt: "2026-08-05T14:00:00Z",
        closedBy: "Gestora Respondente",
        closedAt: "2026-08-05T14:00:00Z",
      },
    ],
  );
});

test("uses an operational label instead of the technical audit action", () => {
  assert.equal(
    reconciliationAuditActionLabel("item_needs_information"),
    "Solicitação de informação registrada",
  );
});
