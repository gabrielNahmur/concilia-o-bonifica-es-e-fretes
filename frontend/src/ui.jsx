import { AlertTriangle, FileText, RefreshCw } from "lucide-react";

export const n = (value, digits = 0) =>
  Number(value || 0).toLocaleString("pt-BR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

export const money = (value, digits = 2) =>
  Number(value || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

export const rateMoney = (value) =>
  Number(value || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  });

export const d = (value) =>
  value
    ? new Date(`${String(value).slice(0, 10)}T12:00:00`).toLocaleDateString("pt-BR")
    : "—";

export const month = (value) =>
  value
    ? new Date(`${String(value).slice(0, 10)}T12:00:00`).toLocaleDateString("pt-BR", {
        month: "short",
        year: "2-digit",
      })
    : "—";

export const isoMonth = () => new Date().toISOString().slice(0, 7);

export const statusMeta = {
  on_track: ["No ritmo", "yellow"], attention: ["No ritmo", "yellow"], risk: ["Atrasado", "red"], late: ["Atrasado", "red"],
  ahead: ["Adiantado", "green"], behind: ["Atrasado", "red"], completed: ["Concluído", "green"], indefinite: ["Por tempo indeterminado", "blue"], pending: ["Pendente", "gray"], confirmed: ["Confirmada", "green"],
  auto_confirmed: ["Confirmada", "green"], manual_confirmed: ["Confirmada", "green"],
  review_required: ["Revisão necessária", "yellow"], data_gap: ["Lacuna de dados", "yellow"],
  needs_information: ["Aguardando informação", "yellow"], partial: ["Parcial", "yellow"],
  late_payment: ["Pago com atraso", "yellow"],
  divergent: ["Divergente", "red"], overdue: ["Vencida", "red"], sent: ["Enviado", "blue"],
  delivered: ["Entregue", "green"], delivery_delayed: ["Entrega atrasada", "yellow"],
  bounced: ["Devolvido", "red"], complained: ["Marcado como spam", "red"],
  generated: ["Gerado", "blue"], generating: ["Gerando", "yellow"], failed: ["Falhou", "red"],
  open: ["Aberta", "red"], in_review: ["Em análise", "yellow"], resolved: ["Resolvida", "green"],
  auto_resolved: ["Resolvida pelo sistema", "blue"], proposed: ["Sugerido", "yellow"],
  accepted: ["Aceito", "green"], rejected: ["Rejeitado", "red"], informative: ["Informativo", "gray"],
  not_applicable: ["Sem valor esperado", "gray"], exact: ["Cadeia exata", "green"],
  incomplete: ["Cadeia incompleta", "yellow"], ambiguous: ["Ambígua", "red"],
  unmatched: ["Não localizada", "red"], probable_upfront: ["Possível antecipação", "yellow"],
  correct: ["Correto", "green"], overcharged: ["Cobrado a mais", "red"],
  undercharged: ["Cobrado a menos", "yellow"], document_mismatch: ["Documento divergente", "red"],
  unpriced: ["Sem histórico", "yellow"], payable_mismatch: ["Título divergente", "red"],
  missing_payable: ["Sem título", "yellow"], canceled: ["Cancelado", "gray"],
  disputed: ["Contestado", "red"], not_required: ["Sem revisão", "green"],
};

export const ruleLabels = {
  distributor_credit: "Crédito na distribuidora", milestone_bonus: "Marcos quadrimestrais",
  invoice_discount: "Desconto em boleto", s10_excess_credit: "Crédito adicional S10",
  bank_deposit: "Depósito em conta",
};

export const supplementalClassificationLabels = {
  pending: "Pendentes", postpaid_regularization: "Regularização da postecipada", upfront: "Antecipação",
  price_difference: "Diferença de preço", commercial_credit: "Crédito comercial", other: "Outros créditos",
};

export const freightIssueLabels = {
  missing_invoice_reference: "NF-e não informada", missing_reference: "Chave de NF-e ausente",
  not_found: "NF-e não localizada", wrong_unit: "NF-e de outra unidade", date_mismatch: "Data da NF-e divergente",
  ambiguous_reference: "Referência ambígua", duplicate_allocation: "NF-e duplicada",
  suggested: "NF-e candidata para confirmação", non_normal_purpose: "Finalidade não normal",
  reference_month_from_access_key: "Competência obtida pela chave", missing_rate: "Sem histórico de tarifa",
  cargo_volume_mismatch: "Volume auxiliar divergente", missing_payable: "Título não localizado",
  payable_mismatch: "Título divergente", payment_pending: "Pagamento pendente",
  canceled_with_payable: "Cancelado com título", manual_invalidated: "Revisão invalidada",
};

export function Badge({ status, children }) {
  const [label, tone] = statusMeta[status] || [children || status || "—", "gray"];
  return <span className={`badge ${tone}`}>{children || label}</span>;
}

export function Progress({ value }) {
  const safe = Math.min(100, Math.max(0, Number(value || 0)));
  return <div className="progress"><span style={{ width: `${safe}%` }} /></div>;
}

export function Loading() {
  return <div className="loading"><RefreshCw className="spin" /> Carregando dados...</div>;
}

export function Empty({ text = "Nenhum registro encontrado." }) {
  return <div className="empty"><FileText />{text}</div>;
}

export function PageHeader({ eyebrow, title, subtitle, actions }) {
  return <div className="page-header"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>{actions && <div className="header-actions">{actions}</div>}</div>;
}

export function Kpi({ icon: Icon, label, value, detail, tone = "green", onClick, ariaLabel }) {
  const content = <><div className={`kpi-icon ${tone}`}><Icon /></div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></>;
  return onClick
    ? <button type="button" className="kpi kpi-button" onClick={onClick} aria-label={ariaLabel || label}>{content}</button>
    : <article className="kpi">{content}</article>;
}

export function SyncWarning({ sync }) {
  if (!sync?.stale) return null;
  return <div className="warning-banner"><AlertTriangle /><div><strong>Dados desatualizados</strong><span>A última sincronização válida tem mais de 24 horas. Os relatórios automáticos estão bloqueados.</span></div></div>;
}
