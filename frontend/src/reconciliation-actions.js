export function reviewActionAvailability(activeItem) {
  const hasItem = Boolean(activeItem?.id);

  return {
    canAcceptLink: hasItem && Math.abs(Number(activeItem.difference_value || 0)) <= 0.01,
    canRequestInformation: hasItem,
    canRejectLink: hasItem,
    unavailableMessage: hasItem
      ? ""
      : "Não há vínculo individual para revisar nesta competência.",
  };
}

function parseReviewNotes(reviewNotes) {
  const value = String(reviewNotes || "").trim();
  const match = value.match(/^\[([^\]]+)\]\s*(.*)$/);
  const rawReason = match?.[1] || "Solicitação interna";

  return {
    reason: rawReason.replaceAll("_", " "),
    notes: match?.[2] || value || "Sem justificativa registrada.",
  };
}

function legacyInformationRequests(items = []) {
  return items
    .filter((item) => item.review_status === "needs_information")
    .map((item) => ({
      itemId: item.id,
      document: item.source_document || "Competência",
      requestedBy: item.reviewed_by || "Administrador",
      requestedAt: item.reviewed_at || null,
      ...parseReviewNotes(item.review_notes),
    }));
}

export function informationRequests(requests = [], legacyItems = []) {
  const persisted = requests
    .filter((request) => request.request_notes != null || request.reason_code != null)
    .map((request) => ({
      id: request.id,
      itemId: request.item_id,
      document: request.document || "Competência",
      status: request.status || "open",
      open: (request.status || "open") === "open",
      reason: String(request.reason_code || "solicitação interna").replaceAll("_", " "),
      notes: request.request_notes || "Sem justificativa registrada.",
      requestedBy: request.requested_by || "Administrador",
      requestedAt: request.requested_at || null,
      response: request.response || null,
      respondedBy: request.responded_by || null,
      respondedAt: request.responded_at || null,
      closedBy: request.closed_by || null,
      closedAt: request.closed_at || null,
    }));
  const openItemIds = new Set(persisted.filter((request) => request.open).map((request) => request.itemId));
  const legacy = legacyInformationRequests(legacyItems.length ? legacyItems : requests)
    .filter((request) => !openItemIds.has(request.itemId))
    .map((request) => ({ ...request, id: `legacy:${request.itemId}`, status: "open", open: true }));

  return [...persisted, ...legacy];
}

export function reconciliationAuditActionLabel(action) {
  return {
    item_information_response: "Resposta de solicitação registrada",
    item_needs_information: "Solicitação de informação registrada",
    item_accept: "Vínculo confirmado",
    item_reject: "Vínculo rejeitado",
  }[action] || action;
}
