import { Component, useEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Building2,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Clock3,
  CreditCard,
  Database,
  Download,
  Eye,
  FileBarChart,
  FileText,
  FileUp,
  Fuel,
  Gauge,
  Landmark,
  LayoutDashboard,
  Link2,
  LogOut,
  Menu,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  TrendingUp,
  Truck,
  Users,
  WalletCards,
  X,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { get, patch, post, remove, upload } from "./api";
import { downloadExcel } from "./excel";
import {
  informationRequests,
  reconciliationAuditActionLabel,
  reviewActionAvailability,
} from "./reconciliation-actions";
import {
  Badge, Empty, Kpi, Loading, PageHeader, Progress, SyncWarning, d,
  freightIssueLabels, isoMonth, money, month, n, rateMoney, ruleLabels,
  statusMeta, supplementalClassificationLabels,
} from "./ui";

function latePaymentDays(title) {
  if (!title?.due_date || !title?.payment_date) return 0;
  const [dueYear, dueMonth, dueDay] = String(title.due_date).slice(0, 10).split("-").map(Number);
  const [paidYear, paidMonth, paidDay] = String(title.payment_date).slice(0, 10).split("-").map(Number);
  if (![dueYear, dueMonth, dueDay, paidYear, paidMonth, paidDay].every(Number.isFinite)) return 0;
  return Math.max(0, Math.round((Date.UTC(paidYear, paidMonth - 1, paidDay) - Date.UTC(dueYear, dueMonth - 1, dueDay)) / 86400000));
}

function LatePaymentWarning({ title }) {
  const days = latePaymentDays(title);
  if (!days) return null;
  return <div className="late-payment-warning"><AlertTriangle aria-hidden="true" /><span>Pagamento em atraso: {days} {days === 1 ? "dia" : "dias"} após o vencimento.</span></div>;
}

function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      onLogin(await post("/auth/login", { email, password }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }
  return (
    <main className="login-page">
      <div className="login-glow" />
      <section className="login-card">
        <div className="login-brand">
          <img src="/logo-gbi.png" alt="GBI Combustíveis" />
          <h1>Bem-vindo</h1>
          <p>
            Acompanhamento inteligente dos contratos e bonificações da rede.
          </p>
          <div className="brand-detail">
            <ShieldCheck />
            <span>Dados protegidos e acesso restrito</span>
          </div>
        </div>
        <div className="login-form-wrap">
          <form onSubmit={submit} className="login-form">
            <div className="mobile-logo">
              <img src="/logo-gbi.png" alt="GBI" />
            </div>
            <p className="eyebrow">GBI COMBUSTÍVEIS</p>
            <h2>Acesso restrito</h2>
            <p className="muted">Entre com as suas credenciais.</p>
            {error && <div className="form-error">{error}</div>}
            <label>
              E-mail
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="seu.email@gbi.com"
                autoFocus
                required
              />
            </label>
            <label>
              Senha
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Sua senha"
                required
              />
            </label>
            <button className="primary wide" disabled={loading}>
              {loading ? (
                <>
                  <RefreshCw className="spin" /> Acessando...
                </>
              ) : (
                "ENTRAR"
              )}
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}

const contractNav = [
  ["/", LayoutDashboard, "Visão geral"],
  ["/contratos", ClipboardCheck, "Contratos"],
  ["/compras", Fuel, "Compras e notas"],
  ["/conciliacoes", WalletCards, "Conciliações"],
  ["/rotina-mensal", Clock3, "Rotina mensal"],
  ["/relatorios", FileBarChart, "Relatórios"],
];

const freightNav = [["/fretes", Truck, "Conciliação de fretes"]];

function BrandLogo({ brand, compact = false }) {
  const normalized = String(brand || "").toUpperCase();
  const meta = {
    IPIRANGA: ["ipiranga", "Ipiranga", "/brand-ipiranga.png"],
    BR: ["br", "BR", "/brand-br.png"],
    SHELL: ["shell", "Shell", "/brand-shell.png"],
    TEXACO: ["texaco", "Texaco", "/brand-texaco.png"],
  }[normalized] || ["other", brand || "Sem bandeira", null];
  return <span className={`brand-logo brand-${meta[0]} ${compact ? "compact" : ""}`} title={meta[1]} aria-label={`Bandeira ${meta[1]}`}>
    {meta[2] ? <img src={meta[2]} alt={meta[1]} /> : <span>{meta[1]}</span>}
  </span>;
}

function unitName(unit, fallback) {
  return unit?.display_name?.replace(/^\d{3}\s*[-—]\s*/, "") || fallback || "Unidade não identificada";
}

function rollingMonthOptions(total = 30) {
  const now = new Date();
  const currentSerial = now.getFullYear() * 12 + now.getMonth();
  return Array.from({ length: total }, (_, index) => {
    const serial = currentSerial - index;
    const year = Math.floor(serial / 12);
    const monthIndex = serial - year * 12;
    const iso = `${year}-${String(monthIndex + 1).padStart(2, "0")}`;
    return { value: iso, label: month(`${iso}-01`) };
  });
}

function MultiSelect({ label, options, value = [], onChange, placeholder = "Todos" }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const selected = options.filter((option) => value.includes(option.value));
  const summary = selected.length === 0 ? placeholder : selected.length === 1 ? selected[0].label : `${selected.length} selecionados`;
  const toggle = (optionValue) => onChange(value.includes(optionValue) ? value.filter((item) => item !== optionValue) : [...value, optionValue]);
  useEffect(() => {
    if (!open) return undefined;
    const closeWhenOutside = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeWhenOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeWhenOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);
  return <div ref={rootRef} className={`multi-select ${open ? "open" : ""}`}>
    {label && <span className="multi-select-label">{label}</span>}
    <button type="button" className="multi-select-trigger" onClick={() => setOpen((current) => !current)} aria-expanded={open}><span>{summary}</span><ChevronRight /></button>
    {open && <div className="multi-select-options" role="listbox" aria-label={label}>
      <button type="button" className="multi-select-clear" onClick={() => onChange([])}>Limpar seleção</button>
      {options.map((option) => <label key={option.value} className="multi-select-option"><input type="checkbox" checked={value.includes(option.value)} onChange={() => toggle(option.value)} /><span>{option.label}</span></label>)}
    </div>}
  </div>;
}

function ExcelExportButton({ filename, sheetName, columns, rows, getRows, disabled = false, onError }) {
  const [exporting, setExporting] = useState(false);
  async function exportRows() {
    setExporting(true);
    try {
      const allRows = getRows ? await getRows() : rows;
      downloadExcel({ filename, sheetName, columns, rows: allRows || [] });
    } catch (error) {
      const message = error.message || "Falha ao exportar a planilha.";
      if (onError) onError(message);
      else window.alert(message);
    } finally {
      setExporting(false);
    }
  }
  return <button type="button" className="secondary table-export" disabled={disabled || exporting} onClick={exportRows}><Download /> {exporting ? "Exportando..." : "Exportar Excel"}</button>;
}

function sumRows(rows, field) {
  return (rows || []).reduce((total, row) => total + Number(row?.[field] || 0), 0);
}

function LoadFailure({ message, onRetry }) {
  return <div className="form-error load-failure" role="alert"><span>{message || "Não foi possível carregar os dados."}</span><button type="button" className="secondary" onClick={onRetry}><RefreshCw /> Tentar novamente</button></div>;
}

function SidebarModule({ title, Icon, items, onNavigate, initiallyOpen = true }) {
  const [open, setOpen] = useState(initiallyOpen);
  return <section className="sidebar-module">
    <button type="button" className="sidebar-module-title" onClick={() => setOpen((current) => !current)} aria-expanded={open}><span><Icon />{title}</span><ChevronRight /></button>
    {open && <div className="sidebar-module-links">{items.map(([path, ItemIcon, label]) => <NavLink key={path} to={path} end={path === "/"} onClick={onNavigate}><ItemIcon />{label}</NavLink>)}</div>}
  </section>;
}

function Layout({ user, onLogout, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="app-shell">
      <aside className={open ? "sidebar open" : "sidebar"}>
        <div className="sidebar-brand">
          <Link
            className="sidebar-logo-link"
            to="/"
            aria-label="Ir para visão geral"
            onClick={() => setOpen(false)}
          >
            <img src="/logo-gbi.png" alt="GBI" />
          </Link>
          <button aria-label="Fechar menu" onClick={() => setOpen(false)}>
            <X />
          </button>
        </div>
        <nav>
          <SidebarModule title="Contratos" Icon={ClipboardCheck} items={contractNav} onNavigate={() => setOpen(false)} />
          <SidebarModule title="Fretes" Icon={Truck} items={[...freightNav, ...(user.role === "admin" ? [["/tarifas-frete", Gauge, "Tarifas de frete"]] : [])]} onNavigate={() => setOpen(false)} />
          {user.role === "admin" && <SidebarModule title="Gestão" Icon={Settings} initiallyOpen={false} items={[["/administracao", Settings, "Administração"]]} onNavigate={() => setOpen(false)} />}
        </nav>
        <div className="sidebar-user">
          <div className="avatar">{user.full_name?.[0] || "U"}</div>
          <div>
            <strong>{user.full_name}</strong>
            <small>
              {user.role === "admin" ? "Administrador" : "Visualizador"}
            </small>
          </div>
          <button title="Sair" onClick={onLogout}>
            <LogOut />
          </button>
        </div>
      </aside>
      {open && (
        <button className="sidebar-backdrop" onClick={() => setOpen(false)} />
      )}
      <div className="main-area">
        <header className="topbar">
          <button className="menu" aria-label="Abrir menu" onClick={() => setOpen(true)}>
            <Menu />
          </button>
          <div>
            <strong>GBI Combustíveis</strong>
            <small>Contratos, compras e fretes</small>
          </div>
          <div className="top-user">
            <span>{user.full_name}</span>
            <Badge status={user.role === "admin" ? "confirmed" : "pending"}>
              {user.role === "admin" ? "Admin" : "Visualizador"}
            </Badge>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}

const openDrawerTokens = [];
let pageOverflowBeforeDrawer = "";

function useDrawerBehavior(onClose) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const token = Symbol("drawer");
    if (openDrawerTokens.length === 0) {
      pageOverflowBeforeDrawer = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    openDrawerTokens.push(token);
    const onKeyDown = (event) => {
      if (event.key === "Escape" && openDrawerTokens.at(-1) === token) {
        onCloseRef.current?.();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const index = openDrawerTokens.indexOf(token);
      if (index >= 0) openDrawerTokens.splice(index, 1);
      if (openDrawerTokens.length === 0) {
        document.body.style.overflow = pageOverflowBeforeDrawer;
      }
    };
  }, []);
}

export function BonusDetailsDrawer({ data, loading, error, onRetry, onClose }) {
  useDrawerBehavior(onClose);
  return (
    <div className="reconciliation-overlay" role="dialog" aria-modal="true" aria-label="Detalhamento da bonificação esperada">
      <button className="drawer-backdrop" onClick={onClose} aria-label="Fechar" />
      <section className="reconciliation-drawer bonus-details-drawer">
        <header className="drawer-header">
          <div>
            <p className="eyebrow">COMPOSIÇÃO DO INDICADOR</p>
            <h2>Bonificação esperada</h2>
            <p>{data?.reference_month ? `Competência ${month(data.reference_month)} • valores e documentos que compõem o card.` : "Carregando composição..."}</p>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Fechar"><X /></button>
        </header>
        <div className="drawer-body">
          {error ? <LoadFailure message={error} onRetry={onRetry} /> : loading || !data ? <Loading /> : <>
            <section className="bonus-detail-totals">
              <div><span>Esperado</span><strong>{money(data.totals.expected_value)}</strong></div>
              <div><span>Identificado</span><strong>{money(data.totals.identified_value)}</strong></div>
              <div className={Math.abs(Number(data.totals.difference_value || 0)) > 0.01 ? "negative" : ""}><span>Diferença</span><strong>{money(data.totals.difference_value)}</strong></div>
              <div><span>Composições</span><strong>{n(data.totals.items)}</strong></div>
            </section>
            {data.items?.length ? <div className="bonus-detail-list">
              {data.items.map((item) => (
                <article className="bonus-detail-item" key={`${item.reconciliation_id}-${item.rule?.id}`}>
                  <header>
                    <div className="bonus-detail-unit"><span className="unit-chip">{item.unit.code}</span><BrandLogo brand={item.unit.brand} compact /><div><strong>{unitName(item.unit, `Unidade ${item.unit.code}`)}</strong><small>{item.rule.label} • vence {d(item.competence.due_date)}</small></div></div>
                    <Badge status={item.status} />
                  </header>
                  <div className="bonus-detail-values"><div><span>Esperado</span><strong>{money(item.expected_value)}</strong></div><div><span>Identificado</span><strong>{money(item.identified_value)}</strong></div><div className={Math.abs(Number(item.difference_value || 0)) > 0.01 ? "negative" : ""}><span>Diferença</span><strong>{money(item.difference_value)}</strong></div><div><span>Base em litros</span><strong>{n(item.purchase_summary?.eligible_liters, 3)} L</strong></div></div>
                  <div className="bonus-detail-context"><span>{item.purchase_summary?.count || 0} NF(s) contratual(is)</span><span>Bruto {money(item.purchase_summary?.gross_value)}</span><span>Títulos {money(item.purchase_summary?.title_value)}</span></div>
                  <details>
                    <summary>Ver notas e títulos usados no cálculo</summary>
                    <div className="bonus-purchase-list">
                      {item.purchases?.map((purchase) => <div className="bonus-purchase" key={purchase.erp_entry_id}>
                        <div><strong>NF {purchase.invoice_number || "não informada"}</strong><span>{d(purchase.purchase_date)} • {purchase.supplier_name || "Fornecedor não informado"}</span></div>
                        <div><strong>{n(purchase.eligible_liters, 3)} L</strong><span>Valor líquido {money(purchase.net_value)}</span></div>
                        <small>{purchase.titles?.length ? purchase.titles.map((title) => `Título ${title.document_id || title.invoice_number || "—"} • vence ${d(title.due_date)} • ${money(title.document_value)}`).join(" | ") : "Título financeiro não localizado"}</small>
                      </div>)}
                    </div>
                  </details>
                  {item.evidence?.length > 0 && <div className="bonus-evidence"><strong>Prova vinculada:</strong> {item.evidence.filter((entry) => entry.counted !== false).map((entry) => `${entry.description || entry.source} (${money(entry.value)})`).join(" • ") || "Em conferência"}</div>}
                </article>
              ))}
            </div> : <Empty text="Nenhuma bonificação esperada para os filtros selecionados." />}
          </>}
        </div>
      </section>
    </div>
  );
}

export function Dashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [selectedMonth, setSelectedMonth] = useState(isoMonth());
  const [selectedUnits, setSelectedUnits] = useState([]);
  const [selectedBrands, setSelectedBrands] = useState([]);
  const [bonusDetails, setBonusDetails] = useState(null);
  const [showBonusDetails, setShowBonusDetails] = useState(false);
  const [bonusLoading, setBonusLoading] = useState(false);
  const [bonusError, setBonusError] = useState("");
  useEffect(() => {
    get(`/dashboard?reference_month=${selectedMonth}-01`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [selectedMonth]);
  const loadBonusDetails = async () => {
    const query = new URLSearchParams({ reference_month: `${selectedMonth}-01` });
    const allowedUnits = (data?.units || [])
      .filter((unit) => (!selectedUnits.length || selectedUnits.includes(unit.code)) && (!selectedBrands.length || selectedBrands.includes(unit.brand)))
      .map((unit) => unit.code);
    allowedUnits.forEach((code) => query.append("unit", code));
    setBonusLoading(true);
    setBonusError("");
    try {
      setBonusDetails(await get(`/dashboard/bonus-details?${query}`));
    } catch (loadError) {
      setBonusDetails(null);
      setBonusError(loadError.message);
    } finally {
      setBonusLoading(false);
    }
  };
  useEffect(() => {
    if (!data) return;
    loadBonusDetails();
  }, [data, selectedMonth, selectedUnits, selectedBrands]);
  if (!data && !error) return <Loading />;
  const unitByCode = Object.fromEntries((data?.units || []).map((unit) => [unit.code, unit]));
  const unitOptions = (data?.units || []).map((unit) => ({ value: unit.code, label: `${unit.code} • ${unitName(unit)}` }));
  const brandOptions = [...new Set((data?.units || []).map((unit) => unit.brand).filter(Boolean))].map((brand) => ({ value: brand, label: brand }));
  const unitVisible = (unit) => (!selectedUnits.length || selectedUnits.includes(unit.code)) && (!selectedBrands.length || selectedBrands.includes(unit.brand));
  const visibleContracts = (data?.contracts || []).filter((row) => unitVisible(unitByCode[row.unit_code] || { code: row.unit_code, brand: row.company_code }));
  const visibleIndefinite = (data?.indefinite_contracts || []).filter((row) => unitVisible(unitByCode[row.unit_code] || { code: row.unit_code, brand: row.company_code }));
  const scopedMonthLiters = [...visibleContracts, ...visibleIndefinite].reduce((total, row) => total + Number(row.month_actual || 0), 0);
  const scopedRemaining = visibleContracts.reduce((total, row) => total + Number(row.remaining_liters || 0), 0);
  const scopedBonus = bonusDetails?.totals;
  return (
    <>
      <PageHeader
        eyebrow="VISÃO EXECUTIVA"
        title="Acompanhamento de contratos"
        subtitle="Volumes, projeções e bonificações em um único painel."
        actions={
          <div className="dashboard-filters">
            <label className="month-filter">
              Competência
              <input
                type="month"
                value={selectedMonth}
                onChange={(e) => setSelectedMonth(e.target.value)}
              />
            </label>
            <MultiSelect label="Unidades" options={unitOptions} value={selectedUnits} onChange={setSelectedUnits} />
            <MultiSelect label="Bandeiras" options={brandOptions} value={selectedBrands} onChange={setSelectedBrands} />
          </div>
        }
      />
      {error && <div className="form-error">{error}</div>}{" "}
      {data && (
        <>
          <SyncWarning sync={data.sync} />
          <section className="kpi-grid">
            <Kpi
              icon={Fuel}
              label="Compras no mês"
              value={`${n(selectedUnits.length || selectedBrands.length ? scopedMonthLiters : data.totals.month_liters)} L`}
              detail={selectedUnits.length || selectedBrands.length ? "Unidades selecionadas" : "Todas as unidades mapeadas"}
            />
            <Kpi
              icon={Gauge}
              label="Saldo contratual"
              value={`${n(selectedUnits.length || selectedBrands.length ? scopedRemaining : data.totals.remaining_liters)} L`}
              detail="Litragem ainda a cumprir"
              tone="blue"
            />
            <Kpi
              icon={WalletCards}
              label="Bonificação esperada"
              value={money(scopedBonus?.expected_value ?? data.totals.bonus_expected)}
              detail="Clique para ver unidades, NFs, litros e títulos"
              tone="orange"
              onClick={() => setShowBonusDetails(true)}
              ariaLabel="Abrir detalhamento da bonificação esperada"
            />
            <Kpi
              icon={CheckCircle2}
              label="Identificada"
              value={money(scopedBonus?.identified_value ?? data.totals.bonus_observed)}
              detail="Conciliação automática + ajustes"
              tone="purple"
            />
          </section>
          <section className="dashboard-grid">
            <article className="panel chart-panel">
              <div className="panel-heading">
                <div>
                  <h2>Evolução de compras</h2>
                  <p>Litros adquiridos nos últimos 12 meses</p>
                </div>
                <TrendingUp />
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={data.series}>
                  <defs>
                    <linearGradient id="fillLiters" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="5%"
                        stopColor="#156fc4"
                        stopOpacity={0.35}
                      />
                      <stop offset="95%" stopColor="#156fc4" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="month" tickFormatter={month} />
                  <YAxis tickFormatter={(v) => `${n(v / 1000)}k`} />
                  <Tooltip
                    formatter={(v) => [`${n(v)} L`, "Compras"]}
                    labelFormatter={month}
                  />
                  <Area
                    dataKey="liters"
                    stroke="#156fc4"
                    fill="url(#fillLiters)"
                    strokeWidth={3}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </article>
            <article className="panel exceptions">
              <div className="panel-heading">
                <div>
                  <h2>Exceções</h2>
                  <p>Itens que pedem atenção</p>
                </div>
                <AlertTriangle />
              </div>
              {data.alerts?.length ? (
                data.alerts.slice(0, 7).map((row, i) => (
                  <Link
                    to={row.id ? "/conciliacoes" : `/unidades/${row.unit_code}`}
                    className="exception-row"
                    key={`${row.unit_code}-${i}`}
                  >
                    <span className="unit-chip">{row.unit_code}</span>
                    <div>
                      <strong>
                        {row.message ||
                          `Contrato em situação de ${statusMeta[row.status]?.[0]?.toLowerCase()}`}
                      </strong>
                      <small>
                        {row.projected_completion
                          ? `Conclusão projetada: ${d(row.projected_completion)}`
                          : "Abra para revisar os detalhes"}
                      </small>
                    </div>
                    <ChevronRight />
                  </Link>
                ))
              ) : (
                <Empty text="Nenhuma exceção na competência." />
              )}
            </article>
          </section>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>Situação por contrato</h2>
                <p>Atingimento, saldo e projeção de término</p>
              </div>
              <Link className="text-link" to="/contratos">
                Ver matriz completa <ChevronRight />
              </Link>
            </div>
            <div className="contract-list">
              {visibleContracts.map((row) => (
                <Link
                  className="contract-row"
                  to={`/unidades/${row.unit_code}`}
                  key={row.unit_code}
                >
                  <div className="contract-unit">
                    <span>{row.unit_code}</span>
                    <div>
                      <strong>{unitName(unitByCode[row.unit_code], `Unidade ${row.unit_code}`)}</strong>
                      <small>
                        {n(row.accumulated_liters)} de {n(row.total_liters)} L
                      </small>
                    </div>
                    <BrandLogo brand={unitByCode[row.unit_code]?.brand || row.company_code} compact />
                  </div>
                  <div className="contract-progress">
                    <div>
                      <span>Contrato</span>
                      <strong>{n(row.contract_percent, 1)}%</strong>
                    </div>
                    <Progress value={row.contract_percent} />
                  </div>
                  <div className="contract-month">
                    <span>Mês</span>
                    <strong>{n(row.month_percent, 1)}%</strong>
                  </div>
                  <div className="contract-date">
                    <span>Conclusão projetada</span>
                    <strong>{d(row.projected_completion)}</strong>
                  </div>
                  <Badge status={row.status} />
                  <ChevronRight />
                </Link>
              ))}
            </div>
          </section>
          {showBonusDetails && <BonusDetailsDrawer data={bonusDetails} loading={bonusLoading} error={bonusError} onRetry={loadBonusDetails} onClose={() => setShowBonusDetails(false)} />}
        </>
      )}
    </>
  );
}

export function Contracts() {
  const [rows, setRows] = useState(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setRows(await get("/units"));
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);
  const filtered = useMemo(
    () =>
      (rows || []).filter((r) =>
        `${r.code} ${r.display_name} ${r.brand}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [rows, search],
  );
  if (loading) return <Loading />;
  if (error) return <LoadFailure message={error} onRetry={load} />;
  return (
    <>
      <PageHeader
        eyebrow="MATRIZ"
        title="Contratos por unidade"
        subtitle="Metas, vigência, benefício efetivo e previsão de conclusão."
        actions={
          <div className="search">
            <Search />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar unidade..."
            />
          </div>
        }
      />
      <div className="matrix-grid">
        {filtered.map((row) => (
          <Link
            to={`/unidades/${row.code}`}
            className="contract-card"
            key={row.code}
          >
            <div className="card-top">
              <div className="unit-large">{row.code}</div>
              <Badge status={row.contract?.status || "pending"}>
                {row.contract
                  ? statusMeta[row.contract.status]?.[0]
                  : "Sem contrato informado"}
              </Badge>
            </div>
            <h2>{row.display_name.replace(/^\d{3}\s*[—-]\s*/, "")}</h2>
            <p><BrandLogo brand={row.brand} /> <span>{row.city || "Cidade a confirmar"}</span></p>
            {row.contract?.contract_type === "fixed_term" ? (
              <>
                <div className="big-progress">
                  <div>
                    <span>Atingimento</span>
                    <strong>{n(row.contract.contract_percent, 1)}%</strong>
                  </div>
                  <Progress value={row.contract.contract_percent} />
                </div>
                <dl>
                  <div>
                    <dt>Meta mensal</dt>
                    <dd>{n(row.contract.monthly_target)} L</dd>
                  </div>
                  <div>
                    <dt>Saldo</dt>
                    <dd>{n(row.contract.remaining_liters)} L</dd>
                  </div>
                  <div>
                    <dt>Vigência</dt>
                    <dd>
                      {d(row.contract.start_date)} — {d(row.contract.end_date)}
                    </dd>
                  </div>
                  <div>
                    <dt>Projeção atual</dt>
                    <dd>{d(row.contract.projected_completion)}</dd>
                  </div>
                  <div>
                    <dt>Benefício efetivo/L</dt>
                    <dd>{money(row.contract.effective_per_liter)}</dd>
                  </div>
                </dl>
              </>
            ) : row.contract?.contract_type === "indefinite" ? (
              <div className="indefinite-contract-card">
                <strong>Contrato por tempo indeterminado</strong>
                <span>Comprado no mês: {n(row.contract.month_actual, 3)} L</span>
                <span>Projeção do mês: {n(row.contract.month_projection, 3)} L</span>
                <span>Bonificação esperada: {money(row.contract.bonus_expected)}</span>
              </div>
            ) : (
              <div className="no-contract">
                <BarChart3 />
                <span>Sem prazo contratual cadastrado; acompanhe compras mensais e anuais.</span>
              </div>
            )}
            <span className="card-link">
              Abrir unidade <ChevronRight />
            </span>
          </Link>
        ))}
      </div>
    </>
  );
}

export function UnitDetail() {
  const { code } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await get(`/units/${code}`));
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    setData(null);
    load();
  }, [code]);
  if (loading) return <Loading />;
  if (error) return <LoadFailure message={error} onRetry={load} />;
  const c = data.contract;
  const trendSeries = (data.series || []).map((row, index, rows) => {
    const window = rows.slice(Math.max(0, index - 2), index + 1);
    return { ...row, trend: window.reduce((sum, item) => sum + Number(item.liters || 0), 0) / window.length };
  });
  return (
    <>
      <PageHeader
        eyebrow={`UNIDADE ${code}`}
        title={data.unit.display_name}
        subtitle={data.unit.city || "Cidade editável no cadastro"}
        actions={
          <><BrandLogo brand={data.unit.brand} /><Link className="secondary" to={`/compras?unit=${code}`}><FileText /> Ver notas</Link></>
        }
      />
      {c?.contract_type === "fixed_term" ? (
        <>
          <section className="kpi-grid">
            <Kpi
              icon={Gauge}
              label="Meta mensal"
              value={`${n(c.monthly_target)} L`}
              detail={`${n(c.month_percent, 1)}% realizado no mês`}
            />
            <Kpi
              icon={Fuel}
              label="Acumulado"
              value={`${n(c.accumulated_liters)} L`}
              detail={`${n(c.contract_percent, 1)}% do contrato`}
              tone="blue"
            />
            <Kpi
              icon={TrendingUp}
              label="Média de 3 meses"
              value={`${n(c.rolling_average)} L`}
              detail={`Projeção do mês: ${n(c.month_projection)} L`}
              tone="orange"
            />
            <Kpi
              icon={Clock3}
              label="Conclusão projetada"
              value={d(c.projected_completion)}
              detail={`Prazo contratual: ${d(c.end_date)}`}
              tone="purple"
            />
          </section>
          <section className="dashboard-grid">
            <article className="panel chart-panel">
              <div className="panel-heading">
                <div>
                  <h2>Compras mensais</h2>
                  <p>Últimos 12 meses</p>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={290}>
                <ComposedChart data={trendSeries}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="month" tickFormatter={month} />
                  <YAxis tickFormatter={(v) => `${n(v / 1000)}k`} />
                  <Tooltip formatter={(v, key) => [`${n(v)} L`, key === "trend" ? "Tendência móvel" : "Compras"]} />
                  <Legend formatter={(value) => value === "trend" ? "Tendência móvel (3 meses)" : "Compras"} />
                  <Bar dataKey="liters" name="liters" fill="#156fc4" radius={[5, 5, 0, 0]} />
                  <Line type="monotone" dataKey="trend" name="trend" stroke="#173f70" strokeWidth={2.5} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </article>
            <article className="panel">
              <div className="panel-heading">
                <div>
                  <h2>Resumo contratual</h2>
                  <p>Vigência e benefícios</p>
                </div>
                <Badge status={c.status} />
              </div>
              <dl className="detail-list">
                <div>
                  <dt>Litragem total</dt>
                  <dd>{n(c.total_liters)} L</dd>
                </div>
                <div>
                  <dt>Saldo remanescente</dt>
                  <dd>{n(c.remaining_liters)} L</dd>
                </div>
                <div>
                  <dt>Antecipada total</dt>
                  <dd>{money(c.upfront_total)}</dd>
                </div>
                <div>
                  <dt>Antecipada por litro</dt>
                  <dd>{money(c.upfront_per_liter)}</dd>
                </div>
                <div>
                  <dt>Postecipada por litro</dt>
                  <dd>{money(c.postpaid_per_liter)}</dd>
                </div>
              </dl>
            </article>
          </section>
        </>
      ) : c?.contract_type === "indefinite" ? (
        <>
          <section className="kpi-grid">
            <Kpi icon={Fuel} label="Compras no mês" value={`${n(c.month_actual, 3)} L`} detail="Compras da companhia atual" />
            <Kpi icon={TrendingUp} label="Projeção do mês" value={`${n(c.month_projection, 3)} L`} detail={`Média de 3 meses: ${n(c.rolling_average, 3)} L`} tone="blue" />
            <Kpi icon={WalletCards} label="Bonificação esperada" value={money(c.bonus_expected)} detail="Na competência atual" tone="orange" />
            <Kpi icon={CheckCircle2} label="Bonificação identificada" value={money(c.bonus_identified)} detail="Conciliação e evidências disponíveis" tone="purple" />
          </section>
          <section className="panel chart-panel">
            <div className="panel-heading"><div><h2>Compras mensais</h2><p>Contrato por tempo indeterminado • tendência móvel de três meses</p></div><BrandLogo brand={data.unit.brand} /></div>
            <ResponsiveContainer width="100%" height={290}><ComposedChart data={trendSeries}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="month" tickFormatter={month} /><YAxis tickFormatter={(v) => `${n(v / 1000)}k`} /><Tooltip formatter={(v, key) => [`${n(v)} L`, key === "trend" ? "Tendência móvel" : "Compras"]} /><Legend formatter={(value) => value === "trend" ? "Tendência móvel (3 meses)" : "Compras"} /><Bar dataKey="liters" name="liters" fill="#156fc4" radius={[5, 5, 0, 0]} /><Line type="monotone" dataKey="trend" name="trend" stroke="#173f70" strokeWidth={2.5} dot={false} /></ComposedChart></ResponsiveContainer>
          </section>
        </>
      ) : (
        <div className="info-banner">
          <BarChart3 />
          <div>
            <strong>Unidade sem prazo ou litragem contratual</strong>
            <span>
              As compras continuam sendo acompanhadas por mês, ano e histórico.
            </span>
          </div>
        </div>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Bonificações</h2>
            <p>Regras válidas para a unidade</p>
          </div>
        </div>
        {data.bonus_rules.length ? (
          <div className="rule-grid">
            {data.bonus_rules.map((r) => (
              <div className="rule-card" key={r.id}>
                <WalletCards />
                <div>
                  <strong>{ruleLabels[r.kind] || r.kind}</strong>
                  <span>
                    {r.rate_per_liter
                      ? `${money(r.rate_per_liter)} por litro`
                      : "Conforme marcos do contrato"}
                  </span>
                  <small>Válida desde {d(r.effective_from)}</small>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty text="Unidade sem bonificação contratual." />
        )}
      </section>
    </>
  );
}

export function PurchaseDetail({ entryId, onClose }) {
  useDrawerBehavior(onClose);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null);
    get(`/purchases/${entryId}`).then(setData).catch((err) => setError(err.message));
  }, [entryId]);
  return <div className="reconciliation-overlay" role="dialog" aria-modal="true" aria-label="Detalhamento da nota de compra">
    <button className="drawer-backdrop" onClick={onClose} aria-label="Fechar" />
    <section className="reconciliation-drawer purchase-drawer">
      <header className="drawer-header"><div><p className="eyebrow">NOTA DE COMPRA</p><h2>{data ? `NF ${data.invoice_number || "não informada"}` : "Carregando nota"}</h2><p>{data ? `${data.unit_code} • ${data.supplier_name || "Fornecedor não informado"}` : "Consulta somente leitura do ERP sincronizado."}</p></div><button className="drawer-close" onClick={onClose} aria-label="Fechar"><X /></button></header>
      <div className="drawer-body">{error && <div className="form-error">{error}</div>}{!data && !error ? <Loading /> : data && <>
        <section className="purchase-hero"><div><span>Emissão</span><strong>{d(data.invoice_issue_date || data.purchase_date)}</strong></div><div><span>Litros</span><strong>{n(data.total_liters, 3)} L</strong></div><div><span>S10</span><strong>{n(data.s10_liters, 3)} L</strong></div><div><span>Valor líquido</span><strong>{money(data.net_value)}</strong></div></section>
        <section className="detail-section"><div className="section-title"><FileText /><div><h3>Dados da nota</h3><p>Registro sincronizado do ERP, sem alteração na origem.</p></div></div><div className="freight-facts"><div><span>Chave de acesso</span><strong>{data.access_key || "Não informada"}</strong></div><div><span>Série</span><strong>{data.invoice_series || "—"}</strong></div><div><span>CNPJ fornecedor</span><strong>{data.supplier_cnpj || "Não informado"}</strong></div><div><span>Companhia mapeada</span><strong>{data.company_code || "Fora do contrato"}</strong></div><div><span>Valor bruto</span><strong>{money(data.gross_value)}</strong></div><div><span>Atualização no ERP</span><strong>{d(data.erp_updated_at)}</strong></div></div></section>
        <section className="detail-section"><div className="section-title section-title-wrap"><div className="section-title-main"><Fuel /><div><h3>Itens da nota</h3><p>Quantidade, produto e valor de cada item adquirido.</p></div></div><ExcelExportButton filename={`nf-${data.invoice_number || entryId}-itens`} sheetName="Itens da nota" rows={data.items} columns={[{ label: "Sequência", value: (item) => item.sequence }, { label: "Código", value: (item) => item.item_code || "" }, { label: "Descrição", value: (item) => item.description || "" }, { label: "Quantidade", value: (item) => Number(item.quantity || 0) }, { label: "Unidade", value: (item) => item.unit || "" }, { label: "Valor unitário", value: (item) => Number(item.unit_value || 0) }, { label: "Total", value: (item) => Number(item.total_value || 0) }]} /></div><div className="table-scroll compact-table"><table><thead><tr><th>Seq.</th><th>Código</th><th>Descrição</th><th className="right">Quantidade</th><th className="right">Vlr. unitário</th><th className="right">Total</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.id}><td>{item.sequence}</td><td>{item.item_code}</td><td>{item.description}</td><td className="right">{n(item.quantity, 3)} {item.unit}</td><td className="right">{money(item.unit_value)}</td><td className="right">{money(item.total_value)}</td></tr>)}</tbody><tfoot><tr><th colSpan="3">Total</th><th className="right">{n(sumRows(data.items, "quantity"), 3)}</th><th /><th className="right">{money(sumRows(data.items, "total_value"))}</th></tr></tfoot></table></div></section>
        <section className="detail-section"><div className="section-title"><CreditCard /><div><h3>Títulos, boletos e baixas</h3><p>Vínculos financeiros encontrados para esta NF.</p></div></div>{data.payables.length ? <div className="purchase-payables">{data.payables.map((title) => <article key={title.id}><header><div><strong>{title.document_id}/{title.sequence}</strong><span>Vence {d(title.due_date)} • {title.payment_date ? `pago em ${d(title.payment_date)}` : "aguardando baixa"}</span></div><div><strong>{money(title.document_value)}</strong><span>Descontos {money(title.other_discount)}</span></div></header>{latePaymentDays(title) > 0 && <LatePaymentWarning title={title} />}{title.movements.length ? <div className="purchase-movements">{title.movements.map((movement) => <div key={movement.erp_key}><span>{d(movement.movement_date)}</span><strong>{money(movement.amount)}</strong><small>{movement.movement_type || "Movimento financeiro"}{movement.payment_method ? ` • ${movement.payment_method}` : ""}{movement.notes ? ` • ${movement.notes}` : ""}</small></div>)}</div> : <small className="muted">Nenhum movimento financeiro vinculado ao título.</small>}</article>)}</div> : <Empty text="Nenhum título financeiro localizado para esta nota." />}</section>
      </>}</div>
    </section>
  </div>;
}

export function Purchases() {
  const params = new URLSearchParams(location.search);
  const initialUnit = params.get("unit");
  const [units, setUnits] = useState([]);
  const [unit, setUnit] = useState(initialUnit ? [initialUnit] : []);
  const [company, setCompany] = useState([]);
  const [reference, setReference] = useState([]);
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sortBy, setSortBy] = useState("purchase_date");
  const [sortDir, setSortDir] = useState("desc");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const requestVersion = useRef(0);
  const buildQuery = (requestedPage = 1, pageSize = 50) => {
    const query = new URLSearchParams({ page: String(requestedPage), page_size: String(pageSize), sort_by: sortBy, sort_dir: sortDir });
    unit.forEach((value) => query.append("unit", value));
    company.forEach((value) => query.append("company", value));
    reference.forEach((value) => query.append("reference_month", value));
    if (appliedSearch) query.set("search", appliedSearch);
    return query;
  };
  const load = async (requestedPage = page) => {
    const currentRequest = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const result = await get(`/purchases?${buildQuery(requestedPage)}`);
      if (currentRequest === requestVersion.current) setData(result);
      return result;
    } catch (loadError) {
      if (currentRequest === requestVersion.current) setError(loadError.message);
      return null;
    } finally {
      if (currentRequest === requestVersion.current) setLoading(false);
    }
  };
  useEffect(() => { get("/units").then(setUnits).catch(() => {}); }, []);
  useEffect(() => { load(page); }, [page, unit, company, reference, sortBy, sortDir, appliedSearch]);
  const monthOptions = rollingMonthOptions();
  const unitOptions = units.map((row) => ({ value: row.code, label: `${row.code} • ${unitName(row)}` }));
  const companyOptions = [{ value: "IPIRANGA", label: "Ipiranga" }, { value: "BR", label: "BR / Vibra" }, { value: "SHELL", label: "Shell / Raízen" }, { value: "TEXACO", label: "Texaco" }];
  const updateFilter = (setter) => (values) => { setPage(1); setter(values); };
  const changeSort = (field) => {
    setPage(1);
    if (field === sortBy) setSortDir((value) => value === "asc" ? "desc" : "asc");
    else { setSortBy(field); setSortDir("asc"); }
  };
  const applySearch = () => {
    setPage(1);
    setAppliedSearch(search.trim());
  };
  const exportRows = async () => {
    const first = await get(`/purchases?${buildQuery(1, 200)}`);
    const rows = [...first.items];
    for (let nextPage = 2; nextPage <= first.pages; nextPage += 1) {
      const response = await get(`/purchases?${buildQuery(nextPage, 200)}`);
      rows.push(...response.items);
    }
    return rows;
  };
  const sortLabel = (field, label) => <button type="button" className="table-sort" onClick={() => changeSort(field)}>{label}{sortBy === field ? (sortDir === "asc" ? " ↑" : " ↓") : ""}</button>;
  return <>
    <PageHeader eyebrow="ORIGEM ERP" title="Compras e notas fiscais" subtitle="Notas, itens, títulos e baixas sincronizados em modo somente leitura." />
    <div className="filters purchase-filters"><MultiSelect label="Unidades" options={unitOptions} value={unit} onChange={updateFilter(setUnit)} /><MultiSelect label="Companhias" options={companyOptions} value={company} onChange={updateFilter(setCompany)} /><MultiSelect label="Competências" options={monthOptions} value={reference} onChange={updateFilter(setReference)} /><label className="grow">Nota, fornecedor, CNPJ ou chave<div className="input-icon"><Search /><input value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && applySearch()} placeholder="Digite para buscar" /></div></label><button className="primary" onClick={applySearch}><Search /> Buscar</button></div>
    {error ? <LoadFailure message={error} onRetry={() => load(page)} /> : loading || !data ? <Loading /> : data.items.length ? <div className="panel table-panel"><div className="table-toolbar"><span>{data.total} registros encontrados</span><ExcelExportButton filename="compras-e-notas" sheetName="Compras" getRows={exportRows} columns={[{ label: "Data", value: (row) => d(row.purchase_date) }, { label: "Unidade", value: (row) => row.unit_code }, { label: "Nota", value: (row) => row.invoice_number || "" }, { label: "Fornecedor", value: (row) => row.supplier_name || "" }, { label: "CNPJ", value: (row) => row.supplier_cnpj || "" }, { label: "Companhia", value: (row) => row.company_code || "Fora do contrato" }, { label: "Litros", value: (row) => Number(row.total_liters || 0) }, { label: "Valor líquido", value: (row) => Number(row.net_value || 0) }, { label: "Chave de acesso", value: (row) => row.access_key || "" }]} /></div><div className="table-scroll"><table><thead><tr><th>{sortLabel("purchase_date", "Data")}</th><th>{sortLabel("unit_code", "Unidade")}</th><th>{sortLabel("invoice_number", "Nota")}</th><th>{sortLabel("supplier_name", "Fornecedor")}</th><th>CNPJ</th><th>{sortLabel("company_code", "Companhia")}</th><th className="right">{sortLabel("total_liters", "Litros")}</th><th className="right">{sortLabel("net_value", "Valor líquido")}</th><th /></tr></thead><tbody>{data.items.map((row) => <tr key={row.erp_entry_id} className="table-clickable" onClick={() => setSelected(row.erp_entry_id)}><td>{d(row.purchase_date)}</td><td><span className="unit-chip">{row.unit_code}</span></td><td><strong>{row.invoice_number || "—"}</strong></td><td><strong>{row.supplier_name || "Não informado"}</strong></td><td>{row.supplier_cnpj || "—"}</td><td>{row.mapped ? <Badge status="confirmed">{row.company_code}</Badge> : <Badge status="pending">Fora do contrato</Badge>}</td><td className="right">{n(row.total_liters, 3)}</td><td className="right">{money(row.net_value)}</td><td><button className="icon-button" onClick={(event) => { event.stopPropagation(); setSelected(row.erp_entry_id); }} aria-label="Abrir nota"><Eye /></button></td></tr>)}</tbody><tfoot><tr><th colSpan="6">Total desta página</th><th className="right">{n(sumRows(data.items, "total_liters"), 3)} L</th><th className="right">{money(sumRows(data.items, "net_value"))}</th><th /></tr></tfoot></table></div><div className="table-footer pagination-footer"><span>{data.total} registros • clique em uma nota para abrir seus itens, títulos e baixas.</span><div className="pagination-actions"><button className="secondary" type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Anterior</button><span>Página {data.page} de {Math.max(data.pages, 1)}</span><button className="secondary" type="button" disabled={page >= data.pages} onClick={() => setPage((value) => value + 1)}>Próxima</button></div></div></div> : <Empty />}
    {selected && <PurchaseDetail entryId={selected} onClose={() => setSelected(null)} />}
  </>;
}

export function FreightOriginSummary({ origin }) {
  if (!origin) return null;
  if (!origin.city) return <>Cidade cadastral não identificada</>;
  return <>Origem cadastral: {origin.city}{origin.state ? `/${origin.state}` : ""}</>;
}

export function FreightOriginFacts({ senderName, senderCnpj, origins = [] }) {
  const singleOrigin = origins.length === 1 ? origins[0] : null;
  return <div><span>Origem cadastral</span><strong>{senderName || singleOrigin?.legal_name || "Não informada"}</strong><small>{senderCnpj || singleOrigin?.cnpj || "—"}</small>{origins.map((origin) => <small key={origin.cnpj}>{origins.length > 1 ? `${origin.legal_name || origin.cnpj} • ` : ""}<span><FreightOriginSummary origin={origin} /></span></small>)}</div>;
}

function FreightDetail({ reconciliationId, user, onClose, onChanged }) {
  useDrawerBehavior(onClose);
  const [detail, setDetail] = useState(null);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setError("");
    try {
      setDetail(await get(`/freights/${reconciliationId}`));
    } catch (err) {
      setError(err.message);
    }
  };
  useEffect(() => {
    load();
  }, [reconciliationId]);
  async function review(action, candidateId = null) {
    if (notes.trim().length < 5) {
      setError("A observação é obrigatória e deve explicar a decisão.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const updated = await post(`/freights/${reconciliationId}/review`, {
        action,
        notes: notes.trim(),
        candidate_purchase_entry_id: candidateId,
        fingerprint: detail.fingerprint,
      });
      setDetail(updated);
      setNotes("");
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  const candidates = detail
    ? detail.invoices.filter((invoice) => invoice.candidate)
    : [];
  return (
    <div className="reconciliation-overlay" role="dialog" aria-modal="true" aria-label="Conferência documental do frete">
      <button className="drawer-backdrop" onClick={onClose} aria-label="Fechar" />
      <section className="reconciliation-drawer freight-drawer">
        <header className="drawer-header">
          <div>
            <p className="eyebrow">CONFERÊNCIA DOCUMENTAL DO FRETE</p>
            <h2>{detail ? `CT-e ${detail.cte_number} • Unidade ${detail.unit_code}` : "Carregando CT-e"}</h2>
            {detail && <p>Competência {d(detail.reference_date)} • {detail.carrier_name} • algoritmo {detail.algorithm_version}</p>}
          </div>
          <div className="drawer-header-actions">
            {detail && <><Badge status={detail.primary_status} /><Badge status={detail.review_status} /></>}
            <button className="drawer-close" onClick={onClose} aria-label="Fechar"><X /></button>
          </div>
        </header>
        <div className="drawer-body">
          {error && <div className="form-error">{error}</div>}
          {!detail ? <Loading /> : (
            <>
              <section className="freight-hero">
                <div><span>Litros comprovados</span><strong>{n(detail.matched_liters, 3)} L</strong></div>
                <div><span>Esperado</span><strong>{detail.has_rate ? money(detail.expected_value) : "—"}</strong></div>
                <div><span>Cobrado no CT-e</span><strong>{money(detail.charged_value)}</strong></div>
                <div><span>Diferença</span><strong className={detail.has_rate && detail.difference_value > 0 ? "negative" : ""}>{detail.has_rate ? money(detail.difference_value) : "—"}</strong></div>
                <div><span>Título lançado</span><strong>{money(detail.payable_value)}</strong></div>
                <div><span>Pago</span><strong>{money(detail.paid_value)}</strong></div>
              </section>
              <div className="detail-alerts">
                {!detail.issues.length && <div className="detail-alert success"><CheckCircle2 /> Todas as evidências estão coerentes.</div>}
                {detail.issues.map((issue, index) => (
                  <div className={`detail-alert ${issue.severity === "critical" ? "danger" : "warning"}`} key={`${issue.code}-${index}`}>
                    <AlertTriangle /><div><strong>{freightIssueLabels[issue.code] || issue.code.replaceAll("_", " ")}</strong><span>{issue.detail}</span></div>
                  </div>
                ))}
              </div>
              <section className="detail-section">
                <div className="section-title"><Truck /><div><h3>CT-e e cobrança</h3><p>{detail.cte.source_kind === "purchase_entry" ? `Documento recebido como entrada de compra (Cd_Entrada ${detail.cte.source_entry_id}).` : "Documento sincronizado pelo identificador técnico Cd_CTe."}</p></div></div>
                <div className="freight-facts">
                  <div><span>Competência da NF-e</span><strong>{d(detail.reference_date)}</strong><small>Data exata quando localizada; mês da chave nas pendências</small></div>
                  <div><span>Emissão registrada no ERP</span><strong>{d(detail.issue_date)}</strong><small>Data original do CT-e</small></div>
                  <div><span>Chave do CT-e</span><strong>{detail.cte.access_key || "Não informada"}</strong></div>
                  <FreightOriginFacts senderName={detail.sender_name} senderCnpj={detail.cte.sender_cnpj} origins={detail.origins || []} />
                  <div><span>Finalidade</span><strong>{detail.cte.purpose === 0 ? "Normal" : `Código ${detail.cte.purpose}`}</strong></div>
                  <div><span>Volume auxiliar da carga</span><strong>{n(detail.cte.cargo_liters, 3)} L</strong><small>Não substitui a NF-e</small></div>
                </div>
              </section>
              <section className="detail-section">
                <div className="section-title"><FileText /><div><h3>NF-es e itens de combustível</h3><p>A quantidade dos itens em litros é a evidência principal.</p></div></div>
                <div className="freight-invoice-list">
                  {detail.invoices.map((invoice) => {
                    const purchase = invoice.resolved || invoice.candidate;
                    return (
                      <article className={invoice.candidate ? "freight-invoice candidate" : "freight-invoice"} key={invoice.id}>
                        <header>
                          <div><strong>{invoice.resolved ? `NF ${invoice.resolved.invoice_number}` : invoice.candidate ? `Sugestão: NF ${invoice.candidate.invoice_number}` : "NF não conciliada"}</strong><span>{invoice.match_reason}</span></div>
                          <Badge status={invoice.resolved ? "confirmed" : invoice.candidate ? "proposed" : "unmatched"} />
                        </header>
                        <small className="freight-key">Referência original: {invoice.reference_invoice_number ? `NF ${invoice.reference_invoice_number}` : "NF sem número"} • {invoice.reference_issue_date ? d(invoice.reference_issue_date) : "data ausente"} • chave {invoice.reference_access_key || "ausente"}</small>
                        {purchase && <>
                          <div className="freight-invoice-summary"><span>Unidade {purchase.unit_code}</span><span>{d(purchase.issue_date)}</span><strong>{n(purchase.total_liters, 3)} L</strong></div>
                          <div className="table-toolbar compact-table-toolbar"><span>Itens da NF {purchase.invoice_number}</span><ExcelExportButton filename={`frete-nf-${purchase.invoice_number || purchase.erp_entry_id}-itens`} sheetName="Itens NF" rows={purchase.items} columns={[{ label: "Item", value: (item) => item.item_code || "" }, { label: "Descrição", value: (item) => item.description || "" }, { label: "Quantidade", value: (item) => Number(item.quantity || 0) }, { label: "Unidade", value: (item) => item.unit || "" }, { label: "Valor", value: (item) => Number(item.total_value || 0) }]} /></div><div className="table-scroll compact-table"><table><thead><tr><th>Item</th><th>Descrição</th><th className="right">Quantidade</th><th className="right">Valor</th></tr></thead><tbody>{purchase.items.map((item, index) => <tr key={`${purchase.erp_entry_id}-${index}`}><td>{item.item_code}</td><td>{item.description}</td><td className="right">{n(item.quantity, 3)} {item.unit}</td><td className="right">{money(item.total_value)}</td></tr>)}</tbody><tfoot><tr><th colSpan="2">Total</th><th className="right">{n(sumRows(purchase.items, "quantity"), 3)}</th><th className="right">{money(sumRows(purchase.items, "total_value"))}</th></tr></tfoot></table></div>
                        </>}
                      </article>
                    );
                  })}
                </div>
              </section>
              <section className="freight-evidence-grid">
                <div className="detail-section">
                  <div className="section-title"><Gauge /><div><h3>Tarifa aplicada</h3><p>Escopo mais específico e vigência da competência da NF-e.</p></div></div>
                  {detail.rate ? <div className="freight-rate-value"><strong>{rateMoney(detail.rate.rate_per_liter)} / L</strong><span>desde {d(detail.rate.effective_from)}{detail.rate.effective_to ? ` até ${d(detail.rate.effective_to)}` : " • sem data final"}</span><small>{detail.rate.scope.unit_code ? `Unidade ${detail.rate.scope.unit_code}` : "Todas as unidades"} • {detail.rate.scope.origin_cnpj || "Todas as origens"}</small></div> : <Empty text="Sem histórico de tarifa nesta competência; somente a cobrança é exibida." />}
                  {detail.origins?.length ? <div className="freight-rate-value">{detail.origins.map((origin) => <small key={origin.cnpj}>{origin.cnpj} • <FreightOriginSummary origin={origin} /></small>)}</div> : null}
                </div>
                <div className="detail-section">
                  <div className="section-title"><CreditCard /><div><h3>Contas a pagar</h3><p>{detail.cte.source_kind === "purchase_entry" ? "Título ligado diretamente pela entrada do CT-e." : "Títulos ligados diretamente pelo Cd_CTe."}</p></div></div>
                  {detail.payables.length ? detail.payables.map((payable) => <div className="freight-payable" key={payable.id}><div><strong>{payable.document_id}/{payable.sequence}</strong><span>Vence {d(payable.due_date)}</span></div><div><strong>{money(payable.document_value)}</strong><span>{payable.payment_date ? `Pago em ${d(payable.payment_date)}` : `Saldo ${money(payable.balance)}`}</span></div></div>) : <Empty text="Título não localizado." />}
                </div>
              </section>
              {user.role === "admin" && <section className="detail-section freight-review-box">
                <div className="section-title"><ShieldCheck /><div><h3>Decisão administrativa</h3><p>Uma fotografia do cálculo será preservada na trilha de auditoria.</p></div></div>
                <label>Observação obrigatória<textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Explique a validação, contestação ou encerramento..." /></label>
                <div className="freight-review-actions">
                  {candidates.map((invoice) => <button className="primary" disabled={busy} onClick={() => review("confirm", invoice.candidate.erp_entry_id)} key={invoice.id}><CheckCircle2 /> Confirmar NF {invoice.candidate.invoice_number}</button>)}
                  <button className="secondary" disabled={busy} onClick={() => review("contest")}><AlertTriangle /> Marcar cobrança contestada</button>
                  <button className="secondary" disabled={busy} onClick={() => review("justify")}><ShieldCheck /> Justificar e encerrar</button>
                  <button className="secondary" disabled={busy} onClick={() => review("reopen")}><RefreshCw /> Reabrir</button>
                </div>
              </section>}
              <section className="detail-section">
                <div className="section-title"><Clock3 /><div><h3>Histórico de decisões</h3><p>Registros imutáveis com o fingerprint analisado.</p></div></div>
                {detail.reviews.length ? <div className="freight-review-history">{detail.reviews.map((review) => <div key={review.id}><Badge status={review.action === "contest" ? "disputed" : "resolved"}>{review.action}</Badge><div><strong>{review.notes}</strong><span>{d(review.created_at)} • usuário {review.created_by}</span></div></div>)}</div> : <Empty text="Nenhuma decisão humana registrada." />}
              </section>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function FreightMetricDetails({ metric, query, onClose, onOpen }) {
  useDrawerBehavior(onClose);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null);
    get(`/freights/card-details?metric=${metric}&${query}`).then(setData).catch((err) => setError(err.message));
  }, [metric, query]);
  return <div className="reconciliation-overlay" role="dialog" aria-modal="true" aria-label="Detalhamento do indicador de fretes">
    <button className="drawer-backdrop" onClick={onClose} aria-label="Fechar" />
    <section className="reconciliation-drawer freight-metric-drawer"><header className="drawer-header"><div><p className="eyebrow">DETALHAMENTO DO INDICADOR</p><h2>{data?.title || "Carregando indicador"}</h2><p>{data?.description || "Mantém os filtros atualmente aplicados."}</p></div><button className="drawer-close" onClick={onClose} aria-label="Fechar"><X /></button></header><div className="drawer-body">{error && <div className="form-error">{error}</div>}{!data && !error ? <Loading /> : data && <><section className="purchase-hero"><div><span>CT-es</span><strong>{n(data.total)}</strong></div><div><span>Valor agregado</span><strong>{metric === "liters" ? `${n(data.total_value, 3)} L` : money(data.total_value)}</strong></div></section>{data.items.length ? <div className="freight-card-items">{data.items.map((row) => <button type="button" key={row.id} onClick={() => onOpen(row.id)}><span>{d(row.reference_date || row.issue_date)}</span><strong>CT-e {row.cte_number}</strong><span>Unidade {row.unit_code} • {row.carrier_name || "Transportador não informado"}</span><b>{row.has_rate ? money(row.difference_value) : row.comparison_reason || "Ver documento"}</b><ChevronRight /></button>)}</div> : <Empty text="Nenhum CT-e compõe este indicador nos filtros atuais." />}</>}</div></section>
  </div>;
}

function Freights({ user }) {
  const [competence, setCompetence] = useState([isoMonth()]);
  const [unit, setUnit] = useState([]);
  const [carrier, setCarrier] = useState("");
  const [status, setStatus] = useState([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [summary, setSummary] = useState(null);
  const [data, setData] = useState(null);
  const [units, setUnits] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedMetric, setSelectedMetric] = useState("");
  const [error, setError] = useState("");
  const requestVersion = useRef(0);
  const load = async (requestedPage = page) => {
    const currentRequest = ++requestVersion.current;
    setError("");
    const params = new URLSearchParams({ page: String(requestedPage), page_size: "50" });
    competence.forEach((value) => params.append("competence", value));
    unit.forEach((value) => params.append("unit", value));
    status.forEach((value) => params.append("status", value));
    if (carrier) params.set("carrier", carrier);
    if (search) params.set("search", search);
    try {
      const [summaryResult, listResult] = await Promise.all([
        get(`/freights/summary?${params}`),
        get(`/freights?${params}`),
      ]);
      if (currentRequest !== requestVersion.current) return;
      setSummary(summaryResult);
      setData(listResult);
    } catch (err) {
      if (currentRequest !== requestVersion.current) return;
      setError(err.message);
    }
  };
  useEffect(() => {
    get("/units").then(setUnits).catch(() => {});
  }, []);
  useEffect(() => {
    setPage(1);
    load(1);
  }, [competence, unit, carrier, status]);
  const monthOptions = rollingMonthOptions();
  const unitOptions = units.map((row) => ({ value: row.code, label: `${row.code} • ${unitName(row)}` }));
  const statusOptions = ["correct", "overcharged", "undercharged", "document_mismatch", "unpriced", "payable_mismatch", "missing_payable"].map((key) => ({ value: key, label: statusMeta[key]?.[0] || key }));
  const detailQuery = useMemo(() => { const params = new URLSearchParams({ page_size: "100" }); competence.forEach((value) => params.append("competence", value)); unit.forEach((value) => params.append("unit", value)); status.forEach((value) => params.append("status", value)); if (carrier) params.set("carrier", carrier); if (search) params.set("search", search); return params.toString(); }, [competence, unit, status, carrier, search]);
  const exportRows = async () => {
    const params = new URLSearchParams({ page: "1", page_size: "200" });
    competence.forEach((value) => params.append("competence", value));
    unit.forEach((value) => params.append("unit", value));
    status.forEach((value) => params.append("status", value));
    if (carrier) params.set("carrier", carrier);
    if (search) params.set("search", search);
    const first = await get(`/freights?${params}`);
    const rows = [...first.items];
    for (let nextPage = 2; nextPage <= first.pages; nextPage += 1) {
      params.set("page", String(nextPage));
      const response = await get(`/freights?${params}`);
      rows.push(...response.items);
    }
    return rows;
  };
  return (
    <>
      <PageHeader eyebrow="VERIFICAÇÃO INDEPENDENTE" title="Conciliação de fretes" subtitle="Compara CT-e, NF-es de combustível, tarifa contratada e contas a pagar sem escrever no ERP." />
      <form className="filters freight-filters" onSubmit={(event) => { event.preventDefault(); setPage(1); load(1); }}>
        <MultiSelect label="Competências" options={monthOptions} value={competence} onChange={setCompetence} placeholder="Todas" />
        <MultiSelect label="Unidades" options={unitOptions} value={unit} onChange={setUnit} />
        <label>Transportador<input value={carrier} onChange={(event) => setCarrier(event.target.value)} placeholder="CNPJ(s), separados por vírgula" /></label>
        <MultiSelect label="Status" options={statusOptions} value={status} onChange={setStatus} />
        <label className="grow">CT-e ou NF-e<div className="input-icon"><Search /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Número ou chave" /></div></label>
        <button className="primary"><Search /> Buscar</button>
      </form>
      {error && <div className="form-error">{error}</div>}
      {!summary || !data ? <Loading /> : <>
        <div className="kpi-grid freight-kpis">
          <Kpi icon={Fuel} label="Litros comprovados" value={`${n(summary.liters)} L`} detail={`${summary.total_ctes} CT-es no filtro • clique para detalhar`} tone="blue" onClick={() => setSelectedMetric("liters")} />
          <Kpi icon={Gauge} label="Frete esperado" value={money(summary.expected, 0)} detail={`${n(summary.priced_ctes)} CT-es comparáveis • clique para detalhar`} tone="purple" onClick={() => setSelectedMetric("expected")} />
          <Kpi icon={Truck} label="Frete cobrado" value={money(summary.charged, 0)} detail="Tot_VlrReceber dos CT-es • clique para detalhar" tone="blue" onClick={() => setSelectedMetric("charged")} />
          <Kpi icon={TrendingUp} label="Diferença líquida" value={money(summary.difference, 0)} detail={summary.uncomparable_ctes ? `${summary.uncomparable_ctes} CT-e(s) sem litros comprovados; fora do cálculo` : "Somente CT-es comparáveis"} tone={summary.difference > 0 ? "orange" : "blue"} onClick={() => setSelectedMetric("difference")} />
          <Kpi icon={CheckCircle2} label="Corretos" value={n(summary.correct)} detail="Conferência exata em centavos • clique para detalhar" tone="green" onClick={() => setSelectedMetric("correct")} />
          <Kpi icon={AlertTriangle} label="Pendências" value={n(summary.pending)} detail="Exigem revisão documental ou financeira • clique para detalhar" tone="orange" onClick={() => setSelectedMetric("pending")} />
        </div>
        {data.items.length ? <div className="panel table-panel freight-table">
          <div className="table-toolbar"><span>{data.total} CT-es encontrados</span><ExcelExportButton filename="conciliacao-de-fretes" sheetName="Fretes" getRows={exportRows} onError={setError} columns={[{ label: "Competência", value: (row) => d(row.reference_date || row.issue_date) }, { label: "CT-e", value: (row) => row.cte_number || "" }, { label: "Unidade", value: (row) => row.unit_code || "" }, { label: "Transportador", value: (row) => row.carrier_name || "" }, { label: "CNPJ transportador", value: (row) => row.carrier_cnpj || "" }, { label: "Litros", value: (row) => Number(row.matched_liters || 0) }, { label: "Frete esperado", value: (row) => row.comparison_available ? Number(row.expected_value || 0) : "" }, { label: "Frete cobrado", value: (row) => Number(row.charged_value || 0) }, { label: "Diferença", value: (row) => row.comparison_available ? Number(row.difference_value || 0) : "" }, { label: "Situação", value: (row) => statusMeta[row.primary_status]?.[0] || row.primary_status }]} /></div>
          <div className="table-scroll"><table><thead><tr><th>Documento</th><th>Unidade</th><th>Transportador</th><th className="right">Litros</th><th>Valores</th><th>Conferência</th><th>Situação</th></tr></thead><tbody>{data.items.map((row) => <tr key={row.id} onDoubleClick={() => setSelected(row.id)}><td data-label="Documento"><strong>CT-e {row.cte_number}</strong><small className="table-subline">{d(row.reference_date || row.issue_date)} • {row.source_kind === "purchase_entry" ? `Entrada ${row.source_entry_id}` : `Cd_CTe ${row.erp_cte_id}`}</small></td><td data-label="Unidade"><span className="unit-chip">{row.unit_code}</span></td><td data-label="Transportador"><strong>{row.carrier_name}</strong><small className="table-subline">{row.carrier_cnpj}</small></td><td data-label="Litros" className="right freight-liters">{n(row.matched_liters)} L</td><td data-label="Valores" className="freight-values"><span><small>Esperado</small><strong>{row.comparison_available ? money(row.expected_value) : "—"}</strong></span><span><small>Cobrado</small><strong>{money(row.charged_value)}</strong></span></td><td data-label="Conferência" className="freight-comparison"><strong className={row.comparison_available && row.difference_value > 0 ? "negative" : ""}>{row.comparison_available ? `Diferença ${money(row.difference_value)}` : "Sem base de comparação"}</strong><small className="table-subline">{row.comparison_available ? "Litros e tarifa comprovados." : row.comparison_reason}</small></td><td data-label="Situação" className="freight-status-cell"><Badge status={row.primary_status} /><button className="icon-button" onClick={() => setSelected(row.id)} aria-label="Abrir detalhe"><Eye /></button></td></tr>)}</tbody><tfoot><tr><th colSpan="3">Total desta página</th><th className="right">{n(sumRows(data.items, "matched_liters"), 3)} L</th><th className="freight-values"><span><small>Esperado</small><strong>{money(data.items.filter((row) => row.comparison_available).reduce((total, row) => total + Number(row.expected_value || 0), 0))}</strong></span><span><small>Cobrado</small><strong>{money(sumRows(data.items, "charged_value"))}</strong></span></th><th className="freight-comparison"><strong>Diferença {money(data.items.filter((row) => row.comparison_available).reduce((total, row) => total + Number(row.difference_value || 0), 0))}</strong></th><th /></tr></tfoot></table></div>
          <div className="table-footer pagination-footer">
            <span>{data.total} registros ativos na competência</span>
            <div className="pagination-actions">
              <button className="secondary" type="button" disabled={page <= 1} onClick={() => { const next = page - 1; setPage(next); load(next); }}>Anterior</button>
              <span>Página {data.page} de {Math.max(data.pages, 1)}</span>
              <button className="secondary" type="button" disabled={page >= data.pages} onClick={() => { const next = page + 1; setPage(next); load(next); }}>Próxima</button>
            </div>
          </div>
        </div> : <Empty text="Nenhum CT-e encontrado para os filtros." />}
      </>}
      {selected && <FreightDetail reconciliationId={selected} user={user} onClose={() => setSelected(null)} onChanged={load} />}
      {selectedMetric && <FreightMetricDetails metric={selectedMetric} query={detailQuery} onClose={() => setSelectedMetric("")} onOpen={(id) => { setSelectedMetric(""); setSelected(id); }} />}
    </>
  );
}

const chainStatus = {
  discount_exact: ["Desconto exato", "green"],
  late_payment: ["Pago com atraso", "yellow"],
  discount_divergent: ["Desconto divergente", "red"],
  pending_payment: ["Aguardando pagamento", "gray"],
  data_gap: ["Lacuna na cadeia", "yellow"],
  financial_linked: ["Financeiro vinculado", "green"],
  title_linked: ["Título localizado", "blue"],
  paid_without_financial_link: ["Pago sem lançamento ligado", "yellow"],
  missing_title: ["Sem título financeiro", "red"],
};

function MatchBadge({ value }) {
  const [label, tone] = chainStatus[value] || [value, "gray"];
  return <span className={`badge ${tone}`}>{label}</span>;
}

function ReconciliationDetail({ reconciliationId, user, onClose, onChanged }) {
  useDrawerBehavior(onClose);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [snapshotMode, setSnapshotMode] = useState(false);
  const loadCurrent = async () => {
    setDetail(null);
    setError("");
    setSnapshotMode(false);
    try {
      setDetail(await get(`/reconciliations/${reconciliationId}/detail`));
    } catch (err) {
      setError(err.message);
    }
  };
  useEffect(() => {
    loadCurrent();
  }, [reconciliationId]);
  async function openSnapshot(reviewId) {
    setBusy(true);
    setError("");
    try {
      setDetail(
        await get(`/reconciliations/${reconciliationId}/reviews/${reviewId}`),
      );
      setSnapshotMode(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (
      !window.confirm(
        `Confirmar toda a conciliação da unidade ${detail.unit_code}?`,
      )
    )
      return;
    setBusy(true);
    try {
      await post(`/reconciliations/${detail.id}/confirm`, {
        notes: "Cadeia documental revisada e confirmada pelo gestor",
      });
      await onChanged();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function adjust() {
    const amount = window.prompt("Valor do ajuste (use ponto para centavos):");
    if (amount === null) return;
    const reason = window.prompt("Motivo do ajuste (obrigatório):");
    if (!reason) return;
    setBusy(true);
    try {
      await post(`/reconciliations/${detail.id}/adjust`, { amount, reason });
      setDetail(await get(`/reconciliations/${detail.id}/detail`));
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function reviewItem(item, action) {
    const labels = {
      accept: "confirmar este vínculo",
      reject: "rejeitar este vínculo",
      needs_information: "solicitar mais informações",
    };
    if (!window.confirm(`Deseja ${labels[action]}?`)) return;
    const reasonCode = window.prompt(
      "Código/motivo curto (ex.: comprovante_validado, origem_incorreta):",
    );
    if (!reasonCode) return;
    const notes = window.prompt("Justificativa detalhada e auditável:");
    if (!notes || notes.length < 5) return;
    setBusy(true);
    setError("");
    try {
      setDetail(
        await post(`/reconciliations/items/${item.id}/review`, {
          action,
          reason_code: reasonCode,
          notes,
        }),
      );
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="reconciliation-overlay" role="dialog" aria-modal="true">
      <button
        className="drawer-backdrop"
        onClick={onClose}
        aria-label="Fechar"
      />
      <section className="reconciliation-drawer">
        <header className="drawer-header">
          <div>
            <p className="eyebrow">
              {snapshotMode ? "FOTOGRAFIA AUDITADA" : "CONFERÊNCIA DOCUMENTAL"}
            </p>
            <h2>
              {detail
                ? `${detail.unit_code} • ${detail.rule.label}`
                : "Carregando conciliação"}
            </h2>
            {detail && (
              <p>
                Competência {month(detail.reference_month)} • Período{" "}
                {d(detail.period_start)} a {d(detail.period_end)}
              </p>
            )}
          </div>
          <div className="drawer-header-actions">
            {snapshotMode && (
              <button className="secondary" onClick={loadCurrent}>
                <RefreshCw /> Voltar ao atual
              </button>
            )}
            <button className="drawer-close" onClick={onClose}>
              <X />
            </button>
          </div>
        </header>
        <div className="drawer-body">
          {error && <div className="form-error">{error}</div>}
          {!detail && !error ? (
            <Loading />
          ) : (
            detail && (
              <>
                <section className="reconciliation-hero">
                  <div className="score-ring">
                    <strong>{detail.score}</strong>
                    <span>/100</span>
                  </div>
                  <div className="hero-values">
                    <div>
                      <span>Esperado</span>
                      <strong>{money(detail.expected_value)}</strong>
                    </div>
                    <div>
                      <span>Identificado</span>
                      <strong>
                        {money(
                          detail.observed_value + detail.manual_adjustment,
                        )}
                      </strong>
                    </div>
                    <div
                      className={
                        Math.abs(detail.difference_value) > 1 ? "negative" : ""
                      }
                    >
                      <span>Diferença</span>
                      <strong>{money(detail.difference_value)}</strong>
                    </div>
                    <div>
                      <span>Vencimento</span>
                      <strong>{d(detail.due_date)}</strong>
                    </div>
                  </div>
                  <Badge status={detail.status} />
                  {detail.confirmation_mode !== "none" && (
                    <small className="confirmation-mode">
                      {detail.confirmation_mode === "automatic"
                        ? "Confirmação automática exata"
                        : "Confirmação manual auditada"}
                    </small>
                  )}
                </section>
                {detail.workspace && (
                  <section className="detail-section workspace-section">
                    <div className="section-title">
                      <ShieldCheck />
                      <div>
                        <h3>Itens conciliáveis e política de precisão</h3>
                        <p>
                          Cada valor esperado possui evidências próprias. Apenas
                          cadeias determinísticas recebem confirmação automática.
                        </p>
                      </div>
                    </div>
                    <div className="workspace-summary">
                      <div>
                        <span>Itens</span>
                        <strong>{detail.workspace.summary.item_count}</strong>
                      </div>
                      <div>
                        <span>Automáticos</span>
                        <strong>{detail.workspace.summary.automatic_confirmed}</strong>
                      </div>
                      <div>
                        <span>Revisados</span>
                        <strong>{detail.workspace.summary.manual_confirmed}</strong>
                      </div>
                      <div>
                        <span>Exceções abertas</span>
                        <strong>{detail.workspace.summary.open_exceptions}</strong>
                      </div>
                    </div>
                    <div className="workspace-item-list">
                      {detail.workspace.items.map((item) => (
                        <article className="workspace-item" key={item.id}>
                          <div className="workspace-item-head">
                            <div>
                              <strong>{item.description}</strong>
                              <span>
                                {d(item.source_date)}
                                {item.source_document
                                  ? ` • Documento ${item.source_document}`
                                  : ""}
                              </span>
                            </div>
                            <Badge status={item.status} />
                          </div>
                          <div className="workspace-item-values">
                            <div>
                              <span>Esperado</span>
                              <strong>{money(item.expected_value)}</strong>
                            </div>
                            <div>
                              <span>Identificado</span>
                              <strong>{money(item.observed_value)}</strong>
                            </div>
                            <div className={Math.abs(item.difference_value) > 0.01 ? "negative" : ""}>
                              <span>Diferença</span>
                              <strong>{money(item.difference_value)}</strong>
                            </div>
                          </div>
                          <p className="policy-reason">
                            <ShieldCheck /> {item.policy_reason}
                          </p>
                          {item.allocations.length > 0 && (
                            <div className="allocation-list">
                              {item.allocations.map((allocation) => (
                                <div key={allocation.id}>
                                  <div>
                                    <strong>
                                      {allocation.evidence.source_type === "BOLETO_PDF"
                                        ? "COMPROVANTE AUXILIAR (NÃO CONTABILIZADO)"
                                        : allocation.evidence.source_type} • {money(
                                        allocation.evidence.counted
                                          ? allocation.allocated_value
                                          : allocation.evidence.value,
                                      )}
                                    </strong>
                                    <span>
                                      {allocation.evidence.document
                                        ? `Documento ${allocation.evidence.document} • `
                                        : ""}
                                      {d(allocation.evidence.date)}
                                    </span>
                                    <small>{allocation.match_basis}</small>
                                    {!allocation.evidence.counted && (
                                      <small>Valor de origem informativo; não compõe o total identificado.</small>
                                    )}
                                  </div>
                                  <Badge
                                    status={
                                      allocation.evidence.counted
                                        ? allocation.match_status === "automatic"
                                          ? "auto_confirmed"
                                          : allocation.match_status
                                        : "pending"
                                    }
                                  >
                                    {!allocation.evidence.counted ? "Não apropriado" : undefined}
                                  </Badge>
                                </div>
                              ))}
                            </div>
                          )}
                          {item.reviewed_at && (
                            <small className="review-note">
                              {item.reviewed_by} • {d(item.reviewed_at)} • {item.review_notes}
                            </small>
                          )}
                          {user.role === "admin" &&
                            !snapshotMode &&
                            item.status !== "auto_confirmed" &&
                            item.status !== "manual_confirmed" && (
                              <div className="workspace-actions">
                                {Math.abs(item.difference_value) <= 0.01 && (
                                  <button className="primary" disabled={busy} onClick={() => reviewItem(item, "accept")}>
                                    <CheckCircle2 /> Aceitar vínculo
                                  </button>
                                )}
                                <button className="secondary" disabled={busy} onClick={() => reviewItem(item, "needs_information")}>
                                  <Clock3 /> Pedir informação
                                </button>
                                <button className="danger-button" disabled={busy} onClick={() => reviewItem(item, "reject")}>
                                  <X /> Rejeitar
                                </button>
                              </div>
                            )}
                        </article>
                      ))}
                    </div>
                    {detail.workspace.exceptions.length > 0 && (
                      <div className="inline-exceptions">
                        <h4>Exceções desta competência</h4>
                        {detail.workspace.exceptions.map((item) => (
                          <div className={`inline-exception ${item.severity}`} key={item.id}>
                            <AlertTriangle />
                            <div>
                              <strong>{item.title}</strong>
                              <span>{item.description}</span>
                            </div>
                            <Badge status={item.status} />
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                )}
                <div className="detail-alerts">
                  {detail.alerts.map((item, index) => (
                    <div
                      className={`detail-alert ${item.severity}`}
                      key={index}
                    >
                      {item.severity === "success" ? (
                        <CheckCircle2 />
                      ) : (
                        <AlertTriangle />
                      )}
                      <span>{item.message}</span>
                    </div>
                  ))}
                </div>
                <section className="detail-section">
                  <div className="section-title">
                    <Link2 />
                    <div>
                      <h3>Como o sistema fez os vínculos</h3>
                      <p>
                        Critérios usados para ligar cada etapa sem depender de
                        aproximações ocultas.
                      </p>
                    </div>
                  </div>
                  <div className="criteria-grid">
                    {detail.criteria.map((item, index) => (
                      <div className="criterion" key={item.step}>
                        <span>{index + 1}</span>
                        <div>
                          <strong>{item.step}</strong>
                          <small>{item.basis}</small>
                        </div>
                        <Badge
                          status={
                            item.quality === "exact"
                              ? "confirmed"
                              : item.quality === "probable"
                                ? "partial"
                                : "pending"
                          }
                        >
                          {item.quality === "exact"
                            ? "Exato"
                            : item.quality === "probable"
                              ? "Provável"
                              : "Sem vínculo"}
                        </Badge>
                      </div>
                    ))}
                  </div>
                  <div className="erp-note">
                    <Database />
                    <span>{detail.erp_order_note}</span>
                  </div>
                </section>
                <section className="detail-section">
                  <div className="section-title">
                    <FileText />
                    <div>
                      <h3>Notas, entradas, títulos e pagamentos</h3>
                      <p>
                        {detail.match_summary.purchase_count} nota(s),{" "}
                        {detail.match_summary.document_count} título(s) e{" "}
                        {detail.match_summary.financial_entry_count}{" "}
                        lançamento(s) financeiro(s).
                      </p>
                    </div>
                  </div>
                  {detail.chains.length ? (
                    <div className="match-chain-list">
                      {detail.chains.map((chain) => (
                        <article
                          className="match-chain"
                          key={chain.purchase.erp_entry_id}
                        >
                          <div className="chain-heading">
                            <div>
                              <span className="unit-chip">
                                {chain.purchase.unit_code}
                              </span>
                              <strong>
                                NF{" "}
                                {chain.purchase.invoice_number || "sem número"}
                              </strong>
                              <small>
                                Emissão {d(chain.purchase.contractual_date)} • entrada {d(chain.purchase.purchase_date)} •{" "}
                                {n(chain.purchase.total_liters, 3)} L •{" "}
                                {money(chain.purchase.net_value)}
                              </small>
                            </div>
                            <MatchBadge value={chain.status} />
                          </div>
                          <div className={`chain-flow ${detail.rule.kind === "invoice_discount" ? "with-settlement" : ""}`}>
                            <div className="flow-node invoice-node">
                              <FileText />
                              <div>
                                <span>NOTA FISCAL</span>
                                <strong>
                                  NF {chain.purchase.invoice_number || "—"} /{" "}
                                  {chain.purchase.invoice_series || "—"}
                                </strong>
                                <small>
                                  {chain.purchase.supplier_name ||
                                    "Fornecedor não informado"}
                                </small>
                                <small>
                                  CNPJ {chain.purchase.supplier_cnpj || "—"}
                                </small>
                              </div>
                            </div>
                            <ArrowRight />
                            <div className="flow-node entry-node">
                              <Database />
                              <div>
                                <span>ENTRADA ERP</span>
                                <strong>
                                  Cd_Entrada {chain.purchase.erp_entry_id}
                                </strong>
                                <small>Chave operacional exata</small>
                                <small>
                                  {chain.purchase.contract_allocated_liters !==
                                  chain.purchase.total_liters
                                    ? `${n(chain.purchase.contract_allocated_liters, 3)} L alocados ao contrato`
                                    : `${n(chain.purchase.eligible_bonus_liters, 3)} de ${n(chain.purchase.total_liters, 3)} L bonificáveis`}
                                </small>
                              </div>
                            </div>
                            <ArrowRight />
                            <div className="flow-node title-node">
                              <CreditCard />
                              <div>
                                <span>TÍTULO ERP</span>
                                <strong>
                                  {chain.documents.length
                                    ? `${chain.documents.length} localizado(s)`
                                    : "Não localizado"}
                                </strong>
                                <small>Vínculo por Cd_Entrada</small>
                              </div>
                            </div>
                            {detail.rule.kind === "invoice_discount" && (
                              <>
                                <ArrowRight />
                                <div className={`flow-node settlement-node ${chain.status}`}>
                                  <WalletCards />
                                  <div>
                                    <span>BAIXA E DESCONTO</span>
                                    <strong>
                                      {money(chain.actual_discount)} de {money(chain.purchase.expected_bonus)}
                                    </strong>
                                    <small>{chain.decision_reason}</small>
                                  </div>
                                </div>
                              </>
                            )}
                          </div>
                          <details className="chain-details">
                            <summary>Conferir itens e documentos</summary>
                            <div className="chain-detail-grid">
                              <div>
                                <h4>Itens da nota</h4>
                                <div className="mini-table">
                                  {chain.purchase.items.map((item) => (
                                    <div
                                      key={`${item.code}-${item.description}`}
                                    >
                                      <span>
                                        {item.code} • {item.description}
                                      </span>
                                      <strong>{n(item.quantity, 3)} L</strong>
                                      <small>{money(item.total_value)}</small>
                                    </div>
                                  ))}
                                </div>
                              </div>
                              <div>
                                <h4>Títulos e lançamentos</h4>
                                {chain.boletos?.length > 0 && (
                                  <div className="boleto-card-list">
                                    <p className="auxiliary-note">
                                      Comprovante auxiliar usado apenas na validação histórica; não participa da decisão automática.
                                    </p>
                                    {chain.boletos.map((boleto) => (
                                      <div className={`boleto-card ${boleto.match_status}`} key={boleto.id}>
                                        <div className="boleto-card-head">
                                          <div>
                                            <strong>{boleto.filename}</strong>
                                            <small>
                                              Documento {boleto.boleto_document_number || "—"} • nosso número {boleto.nosso_numero || "—"}
                                            </small>
                                          </div>
                                          <Badge status="informative">
                                            Auxiliar
                                          </Badge>
                                        </div>
                                        <div className="boleto-values">
                                          <span>Bruto <strong>{money(boleto.gross_value)}</strong></span>
                                          <span>Desconto <strong>{money(boleto.discount_value)}</strong></span>
                                          <span>Líquido <strong>{money(boleto.net_value)}</strong></span>
                                          <span>Vence <strong>{d(boleto.due_date)}</strong></span>
                                        </div>
                                        <p>{boleto.match_basis}</p>
                                        <a className="secondary boleto-file-link" href={boleto.file_url} target="_blank" rel="noreferrer">
                                          <Eye /> Abrir PDF original
                                        </a>
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {chain.documents.length ? (
                                  chain.documents.map((document) => (
                                    <div
                                      className="document-card"
                                      key={document.id}
                                    >
                                      <div className="document-head">
                                        <div>
                                          <strong>
                                            Documento {document.document_id}-
                                            {document.sequence}
                                          </strong>
                                          <small>
                                            Emissão {d(document.issue_date)} •
                                            Vence {d(document.due_date)} • Pago{" "}
                                            {d(document.payment_date)}
                                          </small>
                                        </div>
                                        <span>
                                          {money(document.document_value)}
                                        </span>
                                      </div>
                                      <p>{document.match_basis}</p>
                                      <div className="document-metrics">
                                        <span>
                                          Saldo {money(document.balance)}
                                        </span>
                                        <span>
                                          Vlr_OutAbt{" "}
                                          {money(document.vlr_outabt)}{" "}
                                          (informativo)
                                        </span>
                                        <span>
                                          Baixa bruta {money(document.movement_summary?.gross_settlement)}
                                        </span>
                                        <span>
                                          Desconto D {money(document.movement_summary?.discount)}
                                        </span>
                                        <span>
                                          Juros J {money(document.movement_summary?.interest)}
                                        </span>
                                        <strong>
                                          Líquido {money(document.movement_summary?.net_settlement)}
                                        </strong>
                                      </div>
                                      {document.payable_movements?.length ? (
                                        <div className="movement-list">
                                          {document.payable_movements.map(
                                            (movement) => (
                                              <div
                                                className={`movement-row type-${movement.type.toLowerCase()}`}
                                                key={movement.id}
                                              >
                                                <span>{movement.label}</span>
                                                <strong>
                                                  {money(movement.value)}
                                                </strong>
                                                  <small>
                                                  {d(movement.date)} • baixa {movement.payment_sequence || "—"} • lote {movement.batch || "—"} • {movement.payment_method_label || "modalidade não informada"}
                                                </small>
                                                {movement.type === "D" && (
                                                  <small>
                                                    {movement.corroborated_by_6204
                                                      ? "6204 correspondente localizado"
                                                      : "Sem 6204 correspondente; MDCMP permanece como origem"}
                                                  </small>
                                                )}
                                                {movement.type === "B" && (
                                                  <small>
                                                    {movement.corroborated_by_51
                                                      ? "Histórico 51 correspondente localizado"
                                                      : "Baixa sem histórico 51 único do mesmo valor"}
                                                  </small>
                                                )}
                                              </div>
                                            ),
                                          )}
                                        </div>
                                      ) : (
                                        <div className="no-financial">
                                          Nenhum movimento MDCMP localizado para este título.
                                        </div>
                                      )}
                                      {document.financial_entries.length ? (
                                        <div className="financial-list">
                                          {document.financial_entries.map(
                                            (entry) => (
                                              <div key={entry.id}>
                                                <span
                                                  className={`financial-kind ${entry.kind}`}
                                                >
                                                  {entry.kind === "discount"
                                                    ? "Desconto"
                                                    : entry.kind === "payment"
                                                      ? "Pagamento"
                                                      : "Lançamento"}
                                                </span>
                                                <div>
                                                  <strong>
                                                    {money(entry.value)} •{" "}
                                                    {d(entry.date)}
                                                  </strong>
                                                  <small>
                                                    {entry.history ||
                                                      `Histórico ${entry.history_code}`}
                                                  </small>
                                                  <small>
                                                    {entry.match_basis}
                                                  </small>
                                                </div>
                                              </div>
                                            ),
                                          )}
                                        </div>
                                      ) : (
                                        <div className="no-financial">
                                          Nenhum lançamento MLANF ligado pelo
                                          documento.
                                        </div>
                                      )}
                                    </div>
                                  ))
                                ) : (
                                  <div className="no-financial">
                                    Nenhum título MDCDP ligado a esta entrada.
                                  </div>
                                )}
                              </div>
                            </div>
                          </details>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <Empty text="Nenhuma nota elegível para esta competência." />
                  )}
                </section>
                <section className="detail-section">
                  <div className="section-title">
                    <Landmark />
                    <div>
                      <h3>Evidências da bonificação</h3>
                      <p>
                        Créditos, descontos, depósitos e memórias de cálculo
                        usados no valor identificado.
                      </p>
                    </div>
                  </div>
                  {detail.evidence.length ? (
                    <div className="evidence-table">
                      {detail.evidence.map((item, index) => (
                        <div
                          className="evidence-row"
                          key={`${item.source}-${item.record_id}-${index}`}
                        >
                          <div className={`evidence-icon ${item.kind}`}>
                            {item.kind === "bank" ? (
                              <Landmark />
                            ) : item.kind === "boleto" ? (
                              <FileText />
                            ) : item.kind === "financial" ? (
                              <CreditCard />
                            ) : (
                              <Database />
                            )}
                          </div>
                          <div>
                            <strong>{item.label}</strong>
                            <span>
                              {item.history || "Sem histórico descritivo"}
                            </span>
                            <small>
                              {item.match_basis}
                              {item.document
                                ? ` • Documento ${item.document}`
                                : ""}
                            </small>
                          </div>
                          <div>
                            <strong>
                              {item.value == null ? "—" : money(item.value)}
                            </strong>
                            <span>{d(item.date)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty text="Nenhuma evidência financeira identificada." />
                  )}
                </section>
                {detail.review.adjustments.length > 0 && (
                  <section className="detail-section">
                    <div className="section-title">
                      <Pencil />
                      <div>
                        <h3>Ajustes auditados</h3>
                        <p>
                          Alterações manuais preservadas com responsável e
                          justificativa.
                        </p>
                      </div>
                    </div>
                    <div className="adjustment-list">
                      {detail.review.adjustments.map((item) => (
                        <div key={item.id}>
                          <strong>{money(item.amount)}</strong>
                          <span>{item.reason}</span>
                          <small>
                            {item.created_by} • {d(item.created_at)}
                          </small>
                        </div>
                      ))}
                    </div>
                  </section>
                )}
                {detail.review.history?.length > 0 && (
                  <section className="detail-section">
                    <div className="section-title">
                      <ShieldCheck />
                      <div>
                        <h3>Histórico imutável de revisões</h3>
                        <p>
                          Cada confirmação ou ajuste preserva os valores e
                          vínculos exibidos naquele momento.
                        </p>
                      </div>
                    </div>
                    <div className="review-history">
                      {detail.review.history.map((item) => (
                        <button
                          key={item.id}
                          onClick={() => openSnapshot(item.id)}
                        >
                          <CheckCircle2 />
                          <div>
                            <strong>
                              {item.action === "confirm"
                                ? "Conciliação confirmada"
                                : "Ajuste registrado"}{" "}
                              • {d(item.created_at)}
                            </strong>
                            <span>
                              {item.created_by} • Diferença{" "}
                              {money(item.difference_value)}
                            </span>
                            <small>
                              {item.notes || "Sem observação adicional"}
                            </small>
                          </div>
                          <Eye />
                        </button>
                      ))}
                    </div>
                  </section>
                )}
                <details className="technical-evidence">
                  <summary>Evidência técnica original</summary>
                  <pre>
                    {JSON.stringify(detail.technical_evidence, null, 2)}
                  </pre>
                </details>
              </>
            )
          )}
        </div>
        {detail && user.role === "admin" && !snapshotMode && (
          <footer className="drawer-footer">
            <button className="secondary" onClick={adjust} disabled={busy}>
              <Pencil /> Ajustar
            </button>
            <button
              className="primary"
              onClick={confirm}
              disabled={
                busy ||
                detail.status === "confirmed" ||
                Boolean(
                  detail.workspace &&
                    (detail.workspace.summary.review_required > 0 ||
                      detail.workspace.summary.open_exceptions > 0),
                )
              }
            >
              <CheckCircle2 />{" "}
              {detail.confirmation_mode === "automatic"
                ? "Confirmada automaticamente"
                : detail.review.confirmed_at
                  ? "Revisada por gestor"
                  : "Confirmar conciliação"}
            </button>
          </footer>
        )}
      </section>
    </div>
  );
}

const exceptionLabels = {
  missing_title: "Nota sem título",
  ambiguous_title: "Vínculo de título não único",
  unpaid_title: "Título sem baixa",
  paid_without_discount: "Pago sem desconto",
  partial_discount: "Desconto parcial",
  incomplete_document_chain: "Cadeia incompleta",
  unlinked_evidence: "Evidência sem nota",
  umbrella_allocation_review: "Guarda-chuva",
  origin_not_proven: "Origem não comprovada",
  overdue_difference: "Diferença vencida",
  manual_review_required: "Revisão necessária",
  evidence_overallocated: "Evidência reutilizada",
  unclassified_invoice_discount: "Desconto não classificado",
};

function ExceptionQueue({ user, onOpen }) {
  const [data, setData] = useState(null);
  const [unit, setUnit] = useState("");
  const [severity, setSeverity] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("open");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setError("");
    try {
      const query = new URLSearchParams({
        page_size: 100,
        ...(unit && { unit }),
        ...(severity && { severity }),
        ...(type && { exception_type: type }),
        ...(status && { status }),
      });
      setData(await get(`/reconciliations/exceptions?${query}`));
    } catch (err) {
      setError(err.message);
      setData(null);
    }
  };
  useEffect(() => {
    load();
  }, [unit, severity, type, status]);
  async function act(item, action) {
    let resolutionCode = null;
    let notes = null;
    if (action === "resolve") {
      resolutionCode = window.prompt("Motivo da resolução (ex.: comprovante_validado, nao_procede):");
      if (!resolutionCode) return;
      notes = window.prompt("Conclusão detalhada:");
      if (!notes) return;
    }
    setBusy(true);
    try {
      await post(`/reconciliations/exceptions/${item.id}/action`, {
        action,
        resolution_code: resolutionCode,
        notes,
      });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {error && <LoadFailure message={error} onRetry={load} />}
      {data?.summary && (
        <section className="kpi-grid exception-kpis">
          <Kpi icon={AlertTriangle} label="Exceções abertas" value={n(data.summary.open)} detail={`${n(data.summary.critical)} críticas`} tone="orange" />
          <Kpi icon={WalletCards} label="Valor em risco" value={money(data.summary.value_at_risk)} detail="Diferenças ainda não comprovadas" tone="red" />
          <Kpi icon={Clock3} label="Em análise" value={n(data.summary.in_review)} detail="Com responsável ou tratativa" tone="blue" />
          <Kpi icon={ShieldCheck} label="Política automática" value="100% exata" detail="Aproximações nunca confirmam" tone="purple" />
        </section>
      )}
      <div className="filters exception-filters">
        <label>
          Unidade
          <input value={unit} onChange={(event) => setUnit(event.target.value)} placeholder="Todas" maxLength="3" />
        </label>
        <label>
          Gravidade
          <select value={severity} onChange={(event) => setSeverity(event.target.value)}>
            <option value="">Todas</option>
            <option value="critical">Crítica</option>
            <option value="high">Alta</option>
            <option value="medium">Média</option>
          </select>
        </label>
        <label>
          Tipo
          <select value={type} onChange={(event) => setType(event.target.value)}>
            <option value="">Todos</option>
            {Object.entries(exceptionLabels).map(([value, label]) => (
              <option value={value} key={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Situação
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="open">Abertas + em análise</option>
            <option value="resolved">Resolvidas</option>
            <option value="auto_resolved">Resolvidas pelo sistema</option>
            <option value="">Todas</option>
          </select>
        </label>
      </div>
      {!data ? (
        !error && <Loading />
      ) : data.items.length ? (
        <div className="exception-list">
          {data.items.map((item) => (
            <article className={`exception-card ${item.severity}`} key={item.id}>
              <div className="exception-card-head">
                <div className="unit-large">{item.unit_code}</div>
                <div>
                  <strong>{item.title}</strong>
                  <span>
                    {item.item_description || ruleLabels[item.rule_kind]} • Competência {month(item.reference_month)}
                  </span>
                  <small>{item.description}</small>
                </div>
                <div className="exception-badges">
                  <span className={`severity-badge ${item.severity}`}>{item.severity === "critical" ? "CRÍTICA" : item.severity === "high" ? "ALTA" : "MÉDIA"}</span>
                  <Badge status={item.status} />
                </div>
              </div>
              <div className="exception-values">
                <div><span>Esperado</span><strong>{money(item.expected_value)}</strong></div>
                <div><span>Identificado</span><strong>{money(item.observed_value)}</strong></div>
                <div className="negative"><span>Diferença</span><strong>{money(item.difference_value)}</strong></div>
                <div><span>Vencimento</span><strong>{d(item.due_date)}</strong></div>
              </div>
              {item.resolution_notes && <p className="resolution-note">{item.resolution_code} • {item.resolution_notes}</p>}
              <div className="card-actions">
                <button className="secondary" onClick={() => onOpen(item.reconciliation_id)}><Eye /> Ver cadeia completa</button>
                {user.role === "admin" && item.status === "open" && (
                  <button className="secondary" disabled={busy} onClick={() => act(item, "start_review")}><Clock3 /> Iniciar análise</button>
                )}
                {user.role === "admin" && ["open", "in_review"].includes(item.status) && (
                  <button className="primary" disabled={busy} onClick={() => act(item, "resolve")}><CheckCircle2 /> Registrar resolução</button>
                )}
                {user.role === "admin" && ["resolved", "auto_resolved"].includes(item.status) && (
                  <button className="secondary" disabled={busy} onClick={() => act(item, "reopen")}><RefreshCw /> Reabrir</button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty text="Nenhuma exceção para os filtros selecionados." />
      )}
    </>
  );
}

function ReconciliationCoverage() {
  const [coverage, setCoverage] = useState(null);
  const [error, setError] = useState("");
  const load = async () => {
    setError("");
    try {
      setCoverage(await get("/reconciliations/coverage"));
    } catch (loadError) {
      setCoverage(null);
      setError(loadError.message);
    }
  };
  useEffect(() => {
    load();
  }, []);
  if (error) return <LoadFailure message={error} onRetry={load} />;
  if (!coverage) return <Loading />;
  const summary = coverage.summary;
  const exceptionRisk = [
    "paid_without_discount",
    "contract_discount_shortfall",
    "unclassified_excess_discount",
  ].reduce((total, key) => total + Number(summary.expected_values[key] || 0), 0);
  const causes = [
    ["exact_confirmed", "green", "Cadeia positiva completa"],
    ["late_payment", "yellow", "Título pago após o vencimento; desconto não aplicável"],
    ["paid_without_discount", "red", "Baixa e pagamento comprovados, sem desconto D"],
    ["contract_discount_shortfall", "orange", "Desconto ligado ao título, mas abaixo da regra"],
    ["unclassified_excess_discount", "purple", "Crédito superior sem composição contratual identificável"],
    ["unpaid_overdue", "yellow", "Título vencido ainda sem baixa"],
    ["unpaid_pending", "blue", "Título ainda dentro do prazo financeiro"],
    ["data_gap", "gray", "Falta um elo técnico único"],
  ];
  return (
    <div className="coverage-view">
      <section className="kpi-grid reconciliation-kpis">
        <Kpi icon={FileText} label="Notas monitoradas" value={n(summary.total_items)} detail="005, 007, 014 e 050" />
        <Kpi icon={ShieldCheck} label="Confirmadas exatas" value={n(summary.exact_confirmed)} detail="Nenhuma aproximação de valor" tone="blue" />
        <Kpi icon={AlertTriangle} label="Exceções comprovadas" value={n(summary.settled_exceptions)} detail={`${money(exceptionRisk)} sob cobrança`} tone="orange" />
        <Kpi icon={Clock3} label="Aguardando baixa" value={n(summary.waiting_payment)} detail={`${n(summary.closed_rate, 2)}% já classificados`} tone="purple" />
      </section>

      <section className={`coverage-integrity ${summary.automatic_integrity_violations ? "has-error" : ""}`}>
        <div className="coverage-integrity-icon"><ShieldCheck /></div>
        <div>
          <span>Integridade das confirmações automáticas</span>
          <strong>{n(summary.automatic_integrity_rate, 2)}%</strong>
          <p>
            {summary.automatic_integrity_violations
              ? `${n(summary.automatic_integrity_violations)} confirmação(ões) violaram a política estrita.`
              : "Zero confirmações fora da cadeia exata NF → título → baixa/51 → desconto/6204."}
          </p>
        </div>
        <div className="coverage-progress" aria-label={`Cobertura ${summary.closed_rate}%`}>
          <span style={{ width: `${Math.min(100, summary.closed_rate)}%` }} />
          <small>{n(summary.closed_rate, 2)}% das notas possuem decisão financeira encerrada</small>
        </div>
      </section>

      <section className="coverage-policy">
        <div>
          <span>POLÍTICA ATIVA</span>
          <strong>{coverage.policy.name}</strong>
        </div>
        <p>{coverage.policy.positive_confirmation}</p>
        <p>{coverage.policy.negative_classification}</p>
        <small>Documentos externos não participam do cálculo ou da confirmação.</small>
      </section>

      <section className="coverage-grid">
        <article className="panel coverage-causes">
          <div className="panel-head">
            <div><h3>Resultado por causa</h3><p>Classificação técnica de cada nota monitorada</p></div>
          </div>
          <div className="coverage-cause-list">
            {causes.map(([key, tone, description]) => (
              <div className="coverage-cause" key={key}>
                <span className={`coverage-dot ${tone}`} />
                <div>
                  <strong>{summary.labels[key]}</strong>
                  <small>{description}</small>
                </div>
                <b>{n(summary.counts[key] || 0)}</b>
                <em>{money(summary.expected_values[key] || 0)}</em>
              </div>
            ))}
          </div>
        </article>

        <article className="panel coverage-units">
          <div className="panel-head">
            <div><h3>Cobertura por unidade</h3><p>Confirmações, divergências e títulos abertos</p></div>
            <ExcelExportButton filename="cobertura-por-unidade" sheetName="Cobertura" rows={coverage.units} columns={[{ label: "Unidade", value: (row) => row.unit_code }, { label: "Notas", value: (row) => Number(row.total_items || 0) }, { label: "Exatas", value: (row) => Number(row.exact_confirmed || 0) }, { label: "Exceções", value: (row) => Number(row.settled_exceptions || 0) }, { label: "Aguardando", value: (row) => Number(row.waiting_payment || 0) }, { label: "Encerradas (%)", value: (row) => Number(row.closed_rate || 0) }]} />
          </div>
          <div className="coverage-table-wrap">
            <table className="coverage-table">
              <thead><tr><th>Unidade</th><th>Notas</th><th>Exatas</th><th>Exceções</th><th>Aguardando</th><th>Encerradas</th></tr></thead>
              <tbody>
                {coverage.units.map((row) => (
                  <tr key={row.unit_code}>
                    <td><span className="unit-mini">{row.unit_code}</span></td>
                    <td>{n(row.total_items)}</td>
                    <td className="positive-text">{n(row.exact_confirmed)}</td>
                    <td className="negative-text">{n(row.settled_exceptions)}</td>
                    <td>{n(row.waiting_payment)}</td>
                    <td><strong>{n(row.closed_rate, 2)}%</strong></td>
                  </tr>
                ))}
              </tbody>
              <tfoot><tr><th>Total</th><th>{n(sumRows(coverage.units, "total_items"))}</th><th className="positive-text">{n(sumRows(coverage.units, "exact_confirmed"))}</th><th className="negative-text">{n(sumRows(coverage.units, "settled_exceptions"))}</th><th>{n(sumRows(coverage.units, "waiting_payment"))}</th><th><strong>{n(sumRows(coverage.units, "total_items") ? ((sumRows(coverage.units, "exact_confirmed") + sumRows(coverage.units, "settled_exceptions")) / sumRows(coverage.units, "total_items")) * 100 : 0, 2)}%</strong></th></tr></tfoot>
            </table>
          </div>
        </article>
      </section>
    </div>
  );
}

function CompetencesPanel({ user }) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("");
  const [unit, setUnit] = useState("");
  const [reference, setReference] = useState("");
  const [ruleKind, setRuleKind] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const load = async () => {
    setError("");
    try {
      const query = new URLSearchParams({
        page,
        page_size: 20,
        ...(status && { status }),
        ...(unit && { unit }),
        ...(reference && { reference_month: `${reference}-01` }),
        ...(ruleKind && { rule_kind: ruleKind }),
      });
      setData(await get(`/reconciliations?${query}`));
    } catch (err) {
      setError(err.message);
      setData(null);
    }
  };
  useEffect(() => {
    load();
  }, [status, unit, reference, ruleKind, page]);
  const updateFilter = (setter) => (value) => {
    setPage(1);
    setter(value);
  };
  return (
    <>
      <div className="panel-heading reconciliation-panel-heading"><div><h2>Competências conciliadas</h2><p>Histórico agregado por unidade, regra e competência.</p></div></div>
      {error && <LoadFailure message={error} onRetry={load} />}
      {data?.summary && (
        <section className="kpi-grid reconciliation-kpis">
          <Kpi
            icon={WalletCards}
            label="Esperado"
            value={money(data.summary.expected_value)}
            detail={`${data.total} competências filtradas`}
          />
          <Kpi
            icon={CheckCircle2}
            label="Identificado"
            value={money(data.summary.observed_value)}
            detail="Automático + ajustes auditados"
            tone="blue"
          />
          <Kpi
            icon={AlertTriangle}
            label="Diferença"
            value={money(data.summary.difference_value)}
            detail={`${data.summary.review_required} competência(s) com exceção ou lacuna`}
            tone="orange"
          />
          <Kpi
            icon={ShieldCheck}
            label="Revisadas por gestor"
            value={n(data.summary.manually_reviewed || 0)}
            detail={`${n(data.summary.automatically_confirmed || 0)} confirmações automáticas exatas`}
            tone="purple"
          />
        </section>
      )}
      <div className="filters">
        <label>
          Unidade
          <input
            value={unit}
            onChange={(e) => updateFilter(setUnit)(e.target.value)}
            placeholder="Todas"
            maxLength="3"
          />
        </label>
        <label>
          Competência
          <input
            type="month"
            value={reference}
            onChange={(e) => updateFilter(setReference)(e.target.value)}
          />
        </label>
        <label>
          Modalidade
          <select
            value={ruleKind}
            onChange={(e) => updateFilter(setRuleKind)(e.target.value)}
          >
            <option value="">Todas</option>
            {Object.entries(ruleLabels).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={status}
            onChange={(e) => updateFilter(setStatus)(e.target.value)}
          >
            <option value="">Todos</option>
            {["pending", "confirmed", "partial", "divergent", "overdue"].map(
              (s) => (
                <option value={s} key={s}>
                  {statusMeta[s][0]}
                </option>
              ),
            )}
          </select>
        </label>
      </div>
      {!data ? (
        !error && <Loading />
      ) : data.items.length ? (
        <div className="reconciliation-list detailed-list">
          {data.items.map((row) => (
            <article className="reconciliation-card" key={row.id}>
              <div className="rec-head">
                <div className="unit-large">{row.unit_code}</div>
                <div>
                  <strong>{ruleLabels[row.rule_kind] || row.rule_kind}</strong>
                  <span>
                    Competência {month(row.reference_month)} • Vence{" "}
                    {d(row.due_date)}
                  </span>
                  {row.confirmed_at && (
                    <small>Revisada por gestor em {d(row.confirmed_at)}</small>
                  )}
                  {row.confirmation_mode === "automatic" && (
                    <small>Confirmada automaticamente por cadeia exata</small>
                  )}
                </div>
                <Badge status={row.status} />
              </div>
              <div className="rec-values">
                <div>
                  <span>Esperado</span>
                  <strong>{money(row.expected_value)}</strong>
                </div>
                <div>
                  <span>Identificado</span>
                  <strong>
                    {money(row.observed_value + row.manual_adjustment)}
                  </strong>
                </div>
                <div
                  className={
                    Math.abs(row.difference_value) > 1 ? "negative" : ""
                  }
                >
                  <span>Diferença</span>
                  <strong>{money(row.difference_value)}</strong>
                </div>
                <div>
                  <span>Evidências</span>
                  <strong>{row.evidence_count}</strong>
                  {row.open_exception_count > 0 && (
                    <small>{row.open_exception_count} exceção(ões)</small>
                  )}
                </div>
              </div>
              <div className="card-actions">
                <button
                  className="secondary"
                  onClick={() => setSelected(row.id)}
                >
                  <Eye /> Ver conferência completa
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty text="Nenhuma conciliação para os filtros selecionados." />
      )}
      {data?.pages > 1 && (
        <div className="pagination">
          <button
            className="secondary"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Anterior
          </button>
          <span>
            Página {page} de {data.pages}
          </span>
          <button
            className="secondary"
            disabled={page >= data.pages}
            onClick={() => setPage((p) => p + 1)}
          >
            Próxima
          </button>
        </div>
      )}
      {selected && (
        <ReconciliationDetail
          reconciliationId={selected}
          user={user}
          onClose={() => setSelected(null)}
          onChanged={load}
        />
      )}
    </>
  );
}

const priorityLabels = {
  critical: "Crítica",
  high: "Alta",
  medium: "Média",
  low: "Baixa",
  none: "Informativa",
};

export function QueueChain({ detail, row, activeItem }) {
  const selectedChain =
    detail.chains?.find(
      (chain) =>
        row.document &&
        String(chain.purchase?.invoice_number || "") === String(row.document),
    ) || detail.chains?.[0];
  const title = selectedChain?.documents?.[0];
  const isInvoice = row.rule_kind === "invoice_discount";
  const isCredit = ["distributor_credit", "s10_excess_credit"].includes(
    row.rule_kind,
  );
  const firstEvidence = activeItem?.display_evidence?.[0] || detail.evidence?.[0];
  const hasManagementAdjustment = detail.evidence?.some(
    (evidence) => evidence.source === "MANAGEMENT_ADJUSTMENT",
  );
  const portalCredit = activeItem?.display_evidence?.find(
    (evidence) => evidence.source === "IPIRANGA_PORTAL",
  );
  const portalProofConcluded = Boolean(
    portalCredit && activeItem?.details?.portal_postpaid_exact && activeItem?.status === "auto_confirmed",
  );
  const portalUsage = activeItem?.item_type === "portal_credit_usage" || row.item_type === "portal_credit_usage";

  if (portalUsage) {
    const usageEvidence = activeItem?.display_evidence?.find(
      (evidence) => evidence.source === "IPIRANGA_PORTAL_USAGE",
    ) || firstEvidence;
    const creditValue = usageEvidence?.value ?? activeItem?.observed_value ?? row.observed_value ?? 0;
    const usedInvoice = usageEvidence?.document || row.document || "Não informada";
    return (
      <div className="queue-chain" aria-label="Cadeia do crédito usado no portal">
        <div className="queue-chain-node">
          <span>1. Crédito no portal</span>
          <strong>{money(creditValue)}</strong>
          <small>{usageEvidence?.date ? `Registrado em ${d(usageEvidence.date)}` : "Data informada no extrato"}</small>
        </div>
        <ChevronRight />
        <div className="queue-chain-node">
          <span>2. NF utilizada pelo portal</span>
          <strong>{usedInvoice}</strong>
          <small>Declarada pela Ipiranga como uso do crédito</small>
        </div>
        <ChevronRight />
        <div className="queue-chain-node">
          <span>3. Validação</span>
          <strong>NF e chave conferidas</strong>
          <small>Unidade, fornecedor e documento validados no ERP</small>
        </div>
        <ChevronRight />
        <div className="queue-chain-node">
          <span>4. Crédito confirmado</span>
          <strong>{money(creditValue)}</strong>
          <small>Comprova a utilização; a origem da bonificação é acompanhada no saldo acumulado</small>
        </div>
      </div>
    );
  }

  if (isInvoice) {
    return (
      <div className="queue-chain" aria-label="Cadeia da nota fiscal até o desconto">
        <div className="queue-chain-node">
          <span>1. Nota fiscal</span>
          <strong>{selectedChain?.purchase?.invoice_number || row.document || "Não localizada"}</strong>
          <small>{selectedChain?.purchase?.supplier_name || "Compra contratual"}</small>
        </div>
        <ChevronRight />
        <div className="queue-chain-node">
          <span>2. Título</span>
          <strong>{title?.document_id || "Não localizado"}</strong>
          <small>{title ? `Vencimento ${d(title.due_date)}` : "Sem vínculo no MDCDP"}</small>
        </div>
        <ChevronRight />
        <div className="queue-chain-node">
          <span>3. Baixa</span>
          <strong>{title?.is_paid ? "Baixado" : portalProofConcluded ? "Em aberto no ERP" : "Aguardando baixa"}</strong>
          <small>{title?.payment_date ? `Em ${d(title.payment_date)}` : portalProofConcluded ? "Não bloqueia a bonificação já confirmada no portal" : "Sem pagamento confirmado"}</small>
          {latePaymentDays(title) > 0 && <LatePaymentWarning title={title} />}
        </div>
        <ChevronRight />
        <div className="queue-chain-node">
          <span>{portalCredit ? "4. Crédito no portal" : "4. Desconto"}</span>
          <strong>{money(portalCredit?.value ?? selectedChain?.actual_discount ?? 0)}</strong>
          <small>{portalCredit ? "Extrato do portal confirma a bonificação desta NF" : `Esperado ${money(selectedChain?.purchase?.expected_bonus || row.expected_value)}`}</small>
        </div>
      </div>
    );
  }

  return (
    <div className="queue-chain" aria-label="Cadeia de bonificação por competência">
      <div className="queue-chain-node">
        <span>1. Competência</span>
        <strong>{month(detail.reference_month)}</strong>
        <small>{detail.match_summary?.purchase_count || 0} compra(s) contratual(is)</small>
      </div>
      <ChevronRight />
      <div className="queue-chain-node">
        <span>2. Base da regra</span>
        <strong>{isCredit ? "Volume → crédito" : "Volume → recebimento"}</strong>
        <small>{detail.rule?.formula || ruleLabels[row.rule_kind]}</small>
      </div>
      <ChevronRight />
      <div className="queue-chain-node">
        <span>3. Evidência</span>
        <strong>{firstEvidence?.label || "Não identificada"}</strong>
        <small>{firstEvidence?.date ? d(firstEvidence.date) : "Aguardando vínculo determinístico"}</small>
      </div>
      <ChevronRight />
      <div className="queue-chain-node">
        <span>{hasManagementAdjustment ? "4. Valor regularizado" : "4. Valor identificado"}</span>
        <strong>{money(activeItem?.observed_value ?? (detail.observed_value + detail.manual_adjustment))}</strong>
        <small>{hasManagementAdjustment ? "Ajuste gerencial aprovado" : `Esperado ${money(activeItem?.expected_value ?? detail.expected_value)}`}</small>
      </div>
    </div>
  );
}

function QueueActionForm({ action, onCancel, onSubmit, busy }) {
  const [reasonCode, setReasonCode] = useState("");
  const [notes, setNotes] = useState("");
  const [amount, setAmount] = useState("");
  const labels = {
    accept: ["Confirmar vínculo", "Confirme por que a evidência é suficiente."],
    needs_information: ["Pedir informação", "Registre objetivamente o que falta para concluir."],
    reject: ["Rejeitar vínculo", "Registre por que o vínculo não deve ser considerado."],
    respond_information: ["Responder e encerrar", "A resposta fica registrada no histórico; a pendência financeira não será alterada."],
    adjust: ["Registrar ajuste", "O ajuste é auditável e não altera a regra financeira."],
    confirm: ["Confirmar conciliação", "Registre a confirmação do gestor para esta competência."],
  };
  const [title, hint] = labels[action];
  const requiresReasonCode = ["accept", "needs_information", "reject"].includes(action);
  const submit = (event) => {
    event.preventDefault();
    onSubmit({ reasonCode, notes, amount });
  };
  return (
    <form className="queue-action-form" onSubmit={submit}>
      <div>
        <strong>{title}</strong>
        <small>{hint}</small>
      </div>
      {action === "adjust" && (
        <label>
          Valor do ajuste
          <input
            type="number"
            step="0.01"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            placeholder="0,00"
            required
          />
        </label>
      )}
      {requiresReasonCode && (
        <label>
          Motivo curto
          <input
            value={reasonCode}
            minLength="3"
            maxLength="50"
            onChange={(event) => setReasonCode(event.target.value)}
            placeholder="Ex.: comprovante_validado"
            required
          />
        </label>
      )}
      <label className="queue-action-notes">
        {action === "respond_information" ? "Resposta" : "Justificativa"}
        <textarea
          value={notes}
          minLength="5"
          onChange={(event) => setNotes(event.target.value)}
          placeholder={action === "respond_information" ? "Registre a resposta recebida." : "Descreva a conferência realizada."}
          required
        />
      </label>
      <div className="queue-action-buttons">
        <button className="secondary" type="button" onClick={onCancel} disabled={busy}>Cancelar</button>
        <button className="primary" disabled={busy}>{busy ? "Salvando..." : action === "respond_information" ? "Encerrar solicitação" : "Salvar decisão"}</button>
      </div>
    </form>
  );
}

function QueueReconciliationDetail({ row, user, onClose, onChanged }) {
  useDrawerBehavior(onClose);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeAction, setActiveAction] = useState("");
  const [responseRequestId, setResponseRequestId] = useState("");
  const [file, setFile] = useState(null);
  const isAdmin = user.role === "admin";
  const loadCurrent = async () => {
    setError("");
    try {
      setDetail(await get(`/reconciliations/${row.reconciliation_id}/detail`));
    } catch (err) {
      setError(err.message);
    }
  };
  useEffect(() => {
    setDetail(null);
    setActiveAction("");
    setResponseRequestId("");
    loadCurrent();
  }, [row.reconciliation_id, row.item_id]);
  const activeItem = useMemo(() => {
    if (!detail?.workspace?.items?.length) return null;
    return detail.workspace.items.find((item) => item.id === row.item_id) || detail.workspace.items[0];
  }, [detail, row.item_id]);

  async function submitAction(values) {
    setBusy(true);
    setError("");
    try {
      if (["accept", "needs_information", "reject"].includes(activeAction)) {
        if (!activeItem) throw new Error("Não há item conciliável disponível para revisão.");
        await post(`/reconciliations/items/${activeItem.id}/review`, {
          action: activeAction,
          reason_code: values.reasonCode,
          notes: values.notes,
        });
        await loadCurrent();
      } else if (activeAction === "respond_information") {
        if (!responseRequestId) throw new Error("Selecione a solicitação que será encerrada.");
        await post(`/reconciliations/information-requests/${responseRequestId}/respond`, { notes: values.notes });
        await loadCurrent();
        setResponseRequestId("");
      } else if (activeAction === "adjust") {
        await post(`/reconciliations/${detail.id}/adjust`, {
          amount: values.amount,
          reason: values.notes,
        });
        await loadCurrent();
      } else if (activeAction === "confirm") {
        await post(`/reconciliations/${detail.id}/confirm`, { notes: values.notes });
        await loadCurrent();
      }
      setActiveAction("");
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function uploadBoleto() {
    if (!file || !detail) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await upload(`/reconciliations/${detail.id}/boleto-evidence`, form);
      setDetail(result.detail);
      setFile(null);
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const reviewActions = reviewActionAvailability(activeItem);
  const requests = informationRequests(detail?.information_requests || [], detail?.workspace?.items || []);
  const itemEvidence = activeItem?.display_evidence || detail?.evidence || [];
  const hasManagementAdjustment = detail?.evidence?.some(
    (evidence) => evidence.source === "MANAGEMENT_ADJUSTMENT",
  );
  return (
    <div className="reconciliation-overlay" role="dialog" aria-modal="true" aria-label="Conferência da conciliação">
      <button className="drawer-backdrop" onClick={onClose} aria-label="Fechar" />
      <section className="reconciliation-drawer queue-drawer">
        <header className="drawer-header">
          <div>
            <p className="eyebrow">CONFERÊNCIA DO ITEM</p>
            <h2>{row.unit_code} • {row.document ? `NF ${row.document}` : month(row.reference_month)}</h2>
            <p><BrandLogo brand={row.unit?.brand || row.company_code} compact /> {unitName(row.unit, `Unidade ${row.unit_code}`)} • {ruleLabels[row.rule_kind] || row.rule_kind}</p>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Fechar"><X /></button>
        </header>
        <div className="drawer-body">
          {error && <div className="form-error">{error}</div>}
          {!detail ? <Loading /> : (
            <>
              <section className="queue-detail-summary">
                <div className="queue-what-happens">
                  <span>O que acontece</span>
                  <strong>{row.reason}</strong>
                  <small>Ação sugerida: {row.action_label}</small>
                </div>
                <div className="queue-detail-values">
                <div><span>{row.status === "late_payment" ? "Benefício não aplicável" : "Esperado"}</span><strong>{money(row.status === "late_payment" ? row.contractual_value : row.expected_value)}</strong></div>
                  <div><span>{hasManagementAdjustment ? "Ajuste aprovado" : "Identificado"}</span><strong>{money(row.observed_value)}</strong></div>
                  <div className={Math.abs(Number(row.difference_value)) > 0.01 ? "negative" : ""}><span>Diferença</span><strong>{money(row.difference_value)}</strong></div>
                  <div><span>{row.rule_kind === "invoice_discount" ? "Vencimento do título" : "Vencimento"}</span><strong>{d(row.due_date)}</strong></div>
                </div>
              </section>

              <section className="detail-section queue-primary-section">
                <div className="section-title"><Link2 /><div><h3>Cadeia de conferência</h3><p>Os elos usados para este item, sem repetir dados técnicos.</p></div></div>
                <QueueChain detail={detail} row={row} activeItem={activeItem} />
              </section>

              <section className="detail-section queue-primary-section">
                <div className="section-title"><ShieldCheck /><div><h3>Provas vinculadas</h3><p>Valores, documentos, datas e observações usados para fechar esta conciliação.</p></div></div>
                {itemEvidence.length ? (
                  <div className="queue-evidence-list">
                    {itemEvidence.map((evidence, index) => (
                      <article className="queue-evidence-main" key={`${evidence.source}-${evidence.record_id || evidence.document || index}`}>
                        <div><strong>{evidence.label}</strong><span>{evidence.history || "Sem histórico descritivo"}</span></div>
                        <div><strong>{evidence.value == null ? "—" : money(evidence.value)}</strong><small>{d(evidence.date)}</small></div>
                        <small>{evidence.match_basis}{evidence.document ? ` • Documento ${evidence.document}` : ""}</small>
                      </article>
                    ))}
                  </div>
                ) : <Empty text="Ainda não há prova financeira vinculada a este item." />}
              </section>

              {requests.length > 0 && (
                <section className="detail-section information-requests">
                  <div className="section-title"><Clock3 /><div><h3>Solicitações de informação</h3><p>Itens que dependem de retorno interno antes da conclusão.</p></div></div>
                  <div className="information-request-list">
                    {requests.map((request) => (
                      <article className="information-request-card" key={request.id || request.itemId}>
                        <header>
                          <div><span className={`information-request-state${request.open ? "" : " is-closed"}`}>{request.open ? "Aguardando resposta interna" : "Encerrada"}</span><strong>{request.document === "Competência" ? request.document : `NF ${request.document}`}</strong></div>
                          <small>{request.open ? `Solicitado por ${request.requestedBy} em ${d(request.requestedAt)}` : `Encerrada por ${request.closedBy || request.respondedBy || "Administrador"} em ${d(request.closedAt || request.respondedAt)}`}</small>
                        </header>
                        <div className="information-request-reason"><span>Motivo</span><strong>{request.reason}</strong></div>
                        <p>{request.notes}</p>
                        {request.response && <div className="information-request-response"><span>Resposta de {request.respondedBy || "Administrador"} em {d(request.respondedAt)}</span><p>{request.response}</p></div>}
                        {isAdmin && request.open && !activeAction && <button className="secondary information-request-answer" disabled={busy} onClick={() => { setResponseRequestId(request.id); setActiveAction("respond_information"); }}><Pencil /> Responder e encerrar</button>}
                        {isAdmin && request.open && activeAction === "respond_information" && responseRequestId === request.id && <QueueActionForm action="respond_information" busy={busy} onCancel={() => { setActiveAction(""); setResponseRequestId(""); }} onSubmit={submitAction} />}
                      </article>
                    ))}
                  </div>
                </section>
              )}

              {detail.workspace?.exceptions?.length > 0 && (
                <section className="queue-exception-summary">
                  <AlertTriangle />
                  <div><strong>{detail.workspace.exceptions[0].title}</strong><span>{detail.workspace.exceptions[0].description}</span></div>
                  {detail.workspace.exceptions.length > 1 && <small>+{detail.workspace.exceptions.length - 1} alerta(s) relacionado(s)</small>}
                </section>
              )}

              {isAdmin && (
                <section className="detail-section queue-admin-section">
                  <div className="section-title"><Pencil /><div><h3>Registrar decisão</h3><p>Toda ação exige justificativa e fica gravada na auditoria.</p></div></div>
                  {!activeAction ? (
                    <div className="queue-admin-actions">
                      {reviewActions.canAcceptLink && <button className="primary" disabled={busy} onClick={() => setActiveAction("accept")}><CheckCircle2 /> Confirmar vínculo</button>}
                      {reviewActions.canRequestInformation && <button className="secondary" disabled={busy} onClick={() => setActiveAction("needs_information")}><Clock3 /> Pedir informação</button>}
                      {reviewActions.canRejectLink && <button className="secondary" disabled={busy} onClick={() => setActiveAction("reject")}><X /> Rejeitar vínculo</button>}
                      {!activeItem && <p className="queue-admin-unavailable">{reviewActions.unavailableMessage}</p>}
                      <button className="secondary" disabled={busy} onClick={() => setActiveAction("adjust")}><Pencil /> Registrar ajuste</button>
                      <button className="secondary" disabled={busy || row.state === "confirmed"} onClick={() => setActiveAction("confirm")}><ShieldCheck /> Confirmar competência</button>
                    </div>
                  ) : activeAction !== "respond_information" && <QueueActionForm action={activeAction} busy={busy} onCancel={() => setActiveAction("")} onSubmit={submitAction} />}
                  {row.rule_kind === "invoice_discount" && (
                    <div className="queue-boleto-upload">
                      <div><strong>Anexar boleto</strong><small>Prova auxiliar; a regra financeira não muda por este envio.</small></div>
                      <input type="file" accept="application/pdf" onChange={(event) => setFile(event.target.files?.[0] || null)} />
                      <button className="secondary" disabled={!file || busy} onClick={uploadBoleto}><FileUp /> Anexar</button>
                    </div>
                  )}
                </section>
              )}

              {isAdmin && (
                <details className="technical-evidence queue-technical">
                  <summary>Dados técnicos e auditoria</summary>
                  <div className="queue-audit-list">
                    <p><strong>Itens:</strong> {detail.workspace?.summary?.item_count || 0} • <strong>Exceções abertas:</strong> {detail.workspace?.summary?.open_exceptions || 0}</p>
                    {detail.review?.adjustments?.map((adjustment) => <p key={adjustment.id}>Ajuste {money(adjustment.amount)} • {adjustment.reason} • {d(adjustment.created_at)}</p>)}
                    {detail.review?.history?.map((entry) => <p key={entry.id}>{reconciliationAuditActionLabel(entry.action)} • {entry.created_by} • {d(entry.created_at)} • {entry.notes || "Sem observação"}</p>)}
                  </div>
                  <details className="queue-json"><summary>JSON técnico</summary><pre>{JSON.stringify(detail.technical_evidence, null, 2)}</pre></details>
                </details>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function ReconciliationQueuePanel({ user }) {
  const location = useLocation();
  const queryValues = (key) => new URLSearchParams(location.search).getAll(key).filter(Boolean);
  const [data, setData] = useState(null);
  const [units, setUnits] = useState([]);
  const [unit, setUnit] = useState(() => queryValues("unit"));
  const [company, setCompany] = useState(() => queryValues("company"));
  const [ruleKind, setRuleKind] = useState(() => queryValues("rule_kind"));
  const [reference, setReference] = useState(() => queryValues("reference_month"));
  const initialStates = () => {
    const values = queryValues("state");
    if (values.length) return values;
    const legacyScope = queryValues("scope")[0];
    return legacyScope && legacyScope !== "all" ? [legacyScope] : ["actionable"];
  };
  const [states, setStates] = useState(initialStates);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const load = async () => {
    setError("");
    try {
      const query = new URLSearchParams({ page: String(page), page_size: "30", scope: "all" });
      unit.forEach((value) => query.append("unit", value));
      company.forEach((value) => query.append("company", value));
      ruleKind.forEach((value) => query.append("rule_kind", value));
      reference.forEach((value) => query.append("reference_month", value));
      states.forEach((value) => query.append("state", value));
      setData(await get(`/reconciliations/work-queue?${query}`));
    } catch (err) {
      setError(err.message);
      setData({ items: [], total: 0, pages: 0, summary: { to_treat: 0, value_at_risk: 0, in_review: 0, confirmed: 0 } });
    }
  };
  useEffect(() => { get("/units").then(setUnits).catch(() => {}); }, []);
  useEffect(() => {
    setUnit(queryValues("unit"));
    setCompany(queryValues("company"));
    setRuleKind(queryValues("rule_kind"));
    setReference(queryValues("reference_month"));
    setStates(initialStates());
    setPage(1);
    setSelected(null);
  }, [location.search]);
  useEffect(() => { load(); }, [unit, company, ruleKind, reference, states, page]);
  const updateFilter = (setter) => (value) => { setPage(1); setSelected(null); setter(value); };
  const onlyActionable = states.length === 1 && states[0] === "actionable";
  const waitingCount = Number(data?.summary?.waiting || 0);
  const emptyText = onlyActionable ? "Não há itens que exijam ação nos filtros selecionados." : "Nenhum item para os filtros selecionados.";
  const monthOptions = rollingMonthOptions();
  const unitOptions = units.map((row) => ({ value: row.code, label: `${row.code} • ${unitName(row)}` }));
  const unitByCode = Object.fromEntries(units.map((row) => [row.code, row]));
  const companyOptions = [{ value: "IPIRANGA", label: "Ipiranga" }, { value: "BR", label: "BR / Vibra" }, { value: "SHELL", label: "Shell / Raízen" }, { value: "TEXACO", label: "Texaco" }];
  const ruleOptions = Object.entries(ruleLabels).map(([value, label]) => ({ value, label }));
  const stateOptions = [{ value: "actionable", label: "Para tratar" }, { value: "waiting", label: "Aguardando" }, { value: "confirmed", label: "Confirmados" }];
  return (
    <>
      {error && <div className="form-error">{error}</div>}
      <section className="kpi-grid queue-kpis">
        <Kpi icon={AlertTriangle} label="Itens a tratar" value={n(data?.summary?.to_treat)} detail="Pendências e divergências abertas" tone="orange" />
        <Kpi icon={WalletCards} label="Valor em aberto" value={money(data?.summary?.value_at_risk)} detail="Diferenças financeiras a conferir" tone="red" />
        <Kpi icon={Clock3} label="Em análise" value={n(data?.summary?.in_review)} detail="Itens com tratativa iniciada" tone="blue" />
        <Kpi icon={ShieldCheck} label="Confirmado no período" value={n(data?.summary?.confirmed)} detail="Disponível no histórico" tone="green" />
      </section>
      <section className="queue-filter-panel">
        <div className="filters queue-filters">
          <MultiSelect label="Unidades" options={unitOptions} value={unit} onChange={updateFilter(setUnit)} />
          <MultiSelect label="Companhias" options={companyOptions} value={company} onChange={updateFilter(setCompany)} />
          <MultiSelect label="Modalidades" options={ruleOptions} value={ruleKind} onChange={updateFilter(setRuleKind)} />
          <MultiSelect label="Competências" options={monthOptions} value={reference} onChange={updateFilter(setReference)} />
          <MultiSelect label="Situações" options={stateOptions} value={states} onChange={updateFilter(setStates)} placeholder="Todas" />
        </div>
      </section>
      {!data ? <Loading /> : data.items.length ? (
        <section className="queue-list" aria-live="polite">
          {data.items.map((item) => (
            <article className={`queue-row priority-${item.priority}`} key={item.id}>
              <div className="queue-priority"><span>{priorityLabels[item.priority]}</span><strong>{item.unit_code}</strong></div>
              <div className="queue-main"><strong>{item.document ? `NF ${item.document}` : month(item.reference_month)}</strong><span><BrandLogo brand={unitByCode[item.unit_code]?.brand || item.company_code} compact /> {unitName(unitByCode[item.unit_code], `Unidade ${item.unit_code}`)} • {ruleLabels[item.rule_kind] || item.rule_kind}</span><small>{item.description}</small></div>
              <div className="queue-values"><div><span>{item.status === "late_payment" ? "Benefício não aplicável" : "Esperado"}</span><strong>{money(item.status === "late_payment" ? item.contractual_value : item.expected_value)}</strong></div><div><span>Identificado</span><strong>{money(item.observed_value)}</strong></div><div className={Math.abs(Number(item.difference_value)) > 0.01 ? "negative" : ""}><span>Diferença</span><strong>{money(item.difference_value)}</strong></div></div>
              <div className="queue-missing"><Badge status={item.status} /><strong>{item.action_label}</strong><span>{item.reason}</span><small>Vence {d(item.due_date)}{item.open_exception_count ? ` • ${item.open_exception_count} alerta(s)` : ""}</small></div>
              <button className="secondary queue-open" onClick={() => setSelected({ ...item, unit: unitByCode[item.unit_code] })}><Eye /> Conferir</button>
            </article>
          ))}
        </section>
      ) : onlyActionable && waitingCount > 0 ? <section className="queue-waiting-context"><Empty text="Não há pendência financeira para tratar nos filtros selecionados." /><div className="queue-waiting-actions"><Clock3 /><span>Há {n(waitingCount)} item(ns) aguardando vencimento, baixa ou arquivo externo.</span><button className="secondary" type="button" onClick={() => updateFilter(setStates)(["waiting"])}>Ver itens aguardando</button></div></section> : <Empty text={emptyText} />}
      {data?.pages > 1 && <div className="pagination"><button className="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Anterior</button><span>Página {page} de {data.pages}</span><button className="secondary" disabled={page >= data.pages} onClick={() => setPage((value) => value + 1)}>Próxima</button></div>}
      {selected && <QueueReconciliationDetail row={selected} user={user} onClose={() => setSelected(null)} onChanged={load} />}
    </>
  );
}

export function Reconciliations({ user }) {
  return <>
    <PageHeader
      eyebrow="CONCILIAÇÃO"
      title="Fila de conciliação"
      subtitle="Comece pelo que precisa de ação. Os confirmados ficam no histórico, sem esconder a rastreabilidade."
    />
    <ReconciliationQueuePanel user={user} />
  </>;
}

const routineSituationMeta = {
  automatic: { title: "Fechou automaticamente", badge: "confirmed" },
  awaiting_source: { title: "Aguardando arquivo externo", badge: "pending" },
  awaiting_due: { title: "Aguardando vencimento", badge: "pending" },
  awaiting_settlement: { title: "Aguardando baixa", badge: "pending" },
  awaiting_competence: { title: "Aguardando identificação da competência", badge: "pending" },
  awaiting_portal_credit: { title: "Aguardando crédito no portal", badge: "pending" },
  analysis: { title: "Em análise", badge: "review_required" },
  awaiting_statement: { title: "Aguardando próximo extrato", badge: "pending" },
};

function RaizenReceiptAllocation({ receipt, onSaved }) {
  const [selected, setSelected] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const candidates = receipt.candidate_competencies || [];
  const selectedRows = candidates.filter((row) => selected.includes(row.reference_month));
  const total = selectedRows.reduce((sum, row) => sum + Number(row.expected_value || 0), 0);
  const valid = Math.abs(total - Number(receipt.value || 0)) < 0.005;
  const toggle = (referenceMonth) => setSelected((current) => current.includes(referenceMonth) ? current.filter((item) => item !== referenceMonth) : [...current, referenceMonth]);
  async function submit(event) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    try {
      const result = await post(`/monthly-routine/raizen/054/receipts/${receipt.id}/allocations`, {
        allocations: selectedRows.map((row) => ({ reference_month: row.reference_month, amount: row.expected_value })),
      });
      onSaved(result.routine);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return <article className="routine-receipt">
    <header><div><strong>TED Raízen {receipt.client_commitment || receipt.bank_commitment || "sem identificador"}</strong><span>{d(receipt.credit_date)} • {money(receipt.value)} • comprovante consolidado sem competência definida</span></div><Badge status="review_required">Rateio necessário</Badge></header>
    {!candidates.length ? <div className="detail-alert warning"><AlertTriangle /> Não há competências abertas disponíveis para este comprovante.</div> : <form onSubmit={submit}>
      <p>Selecione as competências que compõem esta TED. O sistema só permite salvar quando a soma for exatamente igual ao comprovante.</p>
      <div className="routine-allocation-options">{candidates.map((row) => <label key={row.reference_month}><input type="checkbox" checked={selected.includes(row.reference_month)} onChange={() => toggle(row.reference_month)} /><span>{month(row.reference_month)} • vence {d(row.due_date)}</span><strong>{money(row.expected_value)}</strong></label>)}</div>
      <div className="routine-allocation-footer"><span>Rateio selecionado: <strong>{money(total)}</strong> de <strong>{money(receipt.value)}</strong></span><button className="primary" disabled={!valid || busy}>{busy ? "Salvando..." : "Salvar rateio auditado"}</button></div>
      {error && <div className="form-error">{error}</div>}
    </form>}
  </article>;
}

export function MonthlyRoutine({ user }) {
  const [data, setData] = useState(null);
  const [units, setUnits] = useState([]);
  const [reference, setReference] = useState([isoMonth()]);
  const [unit, setUnit] = useState([]);
  const [company, setCompany] = useState([]);
  const [sourceType, setSourceType] = useState([]);
  const [situation, setSituation] = useState([]);
  const [uploadingId, setUploadingId] = useState("");
  const [error, setError] = useState("");
  const isAdmin = user.role === "admin";
  const load = async () => {
    setError("");
    try {
      const query = new URLSearchParams();
      reference.forEach((value) => query.append("reference_month", value));
      unit.forEach((value) => query.append("unit", value));
      company.forEach((value) => query.append("company", value));
      sourceType.forEach((value) => query.append("source_type", value));
      situation.forEach((value) => query.append("situation", value));
      setData(await get(`/monthly-routine?${query}`));
    } catch (err) {
      setError(err.message);
      setData(null);
    }
  };
  useEffect(() => { get("/units").then(setUnits).catch(() => {}); }, []);
  useEffect(() => { load(); }, [reference, unit, company, sourceType, situation]);
  const monthOptions = rollingMonthOptions();
  const unitOptions = units.map((row) => ({ value: row.code, label: `${row.code} • ${unitName(row)}` }));
  const companyOptions = [{ value: "IPIRANGA", label: "Ipiranga" }, { value: "BR", label: "BR / Vibra" }, { value: "SHELL", label: "Shell / Raízen" }, { value: "TEXACO", label: "Texaco" }];
  const situationOptions = [{ value: "automatic", label: "Fechou automaticamente" }, { value: "awaiting_source", label: "Aguardando arquivo" }, { value: "awaiting_due", label: "Aguardando vencimento" }, { value: "awaiting_settlement", label: "Aguardando baixa" }, { value: "awaiting_competence", label: "Aguardando competência" }, { value: "awaiting_portal_credit", label: "Aguardando crédito no portal" }, { value: "awaiting_statement", label: "Aguardando próximo extrato" }, { value: "analysis", label: "Em análise" }];
  async function importEvidence(event, card) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = new FormData(form).get("file");
    if (!file || !file.name) return;
    setUploadingId(card.id);
    setError("");
    try {
      const formData = new FormData(form);
      const endpoint = card.source_type === "raizen_receipt" ? "/monthly-routine/imports/raizen/054" : "/monthly-routine/imports/ipiranga";
      await upload(endpoint, formData);
      form.reset();
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploadingId("");
    }
  }
  const groups = ["awaiting_source", "awaiting_due", "awaiting_settlement", "awaiting_competence", "awaiting_portal_credit", "awaiting_statement", "analysis", "automatic"];
  return <>
    <PageHeader eyebrow="OPERAÇÃO DE BONIFICAÇÕES" title="Rotina mensal" subtitle="Veja o que fecha pelo ERP, importe apenas as provas externas necessárias e mantenha cada competência rastreável." />
    {error && <div className="form-error">{error}</div>}
    <section className="kpi-grid routine-kpis">
      <Kpi icon={ShieldCheck} label="Fechados" value={n(data?.summary?.automatic)} detail="Sem nova ação humana" tone="green" />
      <Kpi icon={FileUp} label="Aguardando" value={n(Number(data?.summary?.awaiting_source || 0) + Number(data?.summary?.awaiting_due || 0) + Number(data?.summary?.awaiting_settlement || 0) + Number(data?.summary?.awaiting_competence || 0) + Number(data?.summary?.awaiting_portal_credit || 0) + Number(data?.summary?.awaiting_statement || 0))} detail="Arquivo, vencimento, baixa, competência, crédito ou extrato pendente" tone="orange" />
      <Kpi icon={AlertTriangle} label="Em análise" value={n(data?.summary?.analysis)} detail="Diferença, baixa ou vínculo a acompanhar" tone="red" />
      <Kpi icon={WalletCards} label="Valor em aberto" value={money(data?.summary?.open_value)} detail="Somente competências não concluídas" tone="blue" />
    </section>
    <section className="routine-filter-panel"><div className="filters routine-filters">
      <MultiSelect label="Competências" options={monthOptions} value={reference} onChange={setReference} placeholder="Mês atual" />
      <MultiSelect label="Unidades" options={unitOptions} value={unit} onChange={setUnit} />
      <MultiSelect label="Companhias" options={companyOptions} value={company} onChange={setCompany} />
      <MultiSelect label="Fonte" options={data?.source_options || []} value={sourceType} onChange={setSourceType} />
      <MultiSelect label="Situação" options={situationOptions} value={situation} onChange={setSituation} />
    </div></section>
    {!data ? <Loading /> : <>
      {groups.map((group) => {
        const rows = data.cards.filter((card) => card.situation === group);
        const meta = routineSituationMeta[group];
        const subtitle = group === "automatic" ? "Unidades e competências concluídas ou sem valor liberado no período." : group === "awaiting_source" ? "Importe somente a fonte indicada para cada unidade." : group === "awaiting_due" ? "A competência ainda está dentro do prazo e não exige cobrança ou análise." : group === "awaiting_settlement" ? "Há títulos sem baixa no ERP; o valor não é tratado como falta de bonificação até a liquidação." : group === "awaiting_competence" ? "O extrato contém crédito, mas não identifica a competência; a fila abre em Aguardando, sem criar divergência financeira." : group === "awaiting_portal_credit" ? "O extrato foi importado, mas ainda não contém um crédito vinculável à competência; a fila abre em Aguardando." : group === "awaiting_statement" ? "Há saldo de compras posteriores ao último extrato; acompanhe a próxima atualização sem criar pendência por NF." : "Há evidência, baixa ou vínculo que ainda precisa acompanhar.";
        return <section className="routine-section" key={group}>
          <div className="section-title"><div><h3>{meta.title}</h3><p>{subtitle}</p></div><Badge status={meta.badge}>{rows.length}</Badge></div>
          {rows.length ? <div className="routine-card-grid">{rows.map((card) => <article className={`routine-card routine-${card.situation}`} key={card.id}>
            <header><div className="routine-card-unit"><span className="routine-unit-code">{card.unit_code}</span><BrandLogo brand={card.brand || card.company_code} compact /><div><strong>{unitName({ display_name: card.unit_name }, `Unidade ${card.unit_code}`)}</strong><span>{card.company_code} • {card.rule_label}</span></div></div><Badge status={meta.badge}>{card.confirmation_mode === "automatic" && card.situation === "automatic" ? "Automático" : meta.title}</Badge></header>
            <div className="routine-card-values"><div><span>{card.cumulative ? "Esperado acumulado" : "Esperado"}</span><strong>{money(card.expected_value)}</strong></div><div><span>{card.cumulative ? "Créditos apropriados" : "Identificado"}</span><strong>{money(card.observed_value)}</strong></div><div className={Math.abs(Number(card.difference_value || 0)) > 0.01 ? "negative" : ""}><span>{card.cumulative ? "Saldo efetivo" : "Diferença"}</span><strong>{money(card.difference_value)}</strong></div></div>
            {card.cumulative && <div className="routine-cumulative-note">
              <span>Até {d(card.as_of_date)} • <strong>{n(card.credit_event_count)} crédito(s) postecipado(s) no portal</strong></span>
              {card.latest_import ? <small>Último extrato: {card.latest_import.original_filename} • Importado em {d(card.latest_import.created_at)}{card.latest_import.latest_credit_date ? ` • Créditos emitidos até ${d(card.latest_import.latest_credit_date)}` : ""} • {n(card.latest_import.imported_count)} crédito(s) novo(s).</small> : <small>Nenhum extrato Ipiranga foi importado ainda.</small>}
              {Number(card.portal_unallocated_value || 0) > 0.01 && <small>Crédito residual no portal: {money(card.portal_unallocated_value)}. Créditos brutos emitidos: {money(card.portal_credit_total_value)}. Este saldo não representa diferença contratual.</small>}
              {Number(card.historical_adjustment_value || 0) > 0.01 && <small>{card.adjustment_note || "Ajuste histórico acompanhado"}: {money(card.historical_adjustment_value)}. Saldo antes do ajuste: {money(card.portal_difference_value)}.</small>}
              {Number(card.next_statement_expected_value || 0) > 0.01 && <small>Compras posteriores aguardando extrato: {money(card.next_statement_expected_value)}.</small>}
            </div>}
            <div className="routine-next-action"><FileUp /><div><strong>{card.action}</strong><span>{card.description}</span>{card.due_date && <small>Vencimento da competência: {d(card.due_date)}</small>}</div></div>
            {isAdmin && card.source_type !== "erp" && card.situation !== "automatic" && <form className="routine-upload" onSubmit={(event) => importEvidence(event, card)}>{card.source_type !== "raizen_receipt" && <input type="hidden" name="unit_code" value={card.unit_code} />}<label><span>{card.source.label} ({card.source.formats.join(", ")})</span><input name="file" type="file" accept={card.source.formats.join(",")} required /></label><button className="primary" disabled={uploadingId === card.id}><FileUp /> {uploadingId === card.id ? "Importando..." : card.situation === "analysis" || card.imports.length ? "Importar atualização" : "Importar"}</button></form>}
            {card.imports.length > 0 && <details className="routine-files"><summary>{card.imports.length} arquivo(s) vinculado(s) {card.cumulative ? "à unidade" : "à competência"}</summary>{card.imports.map((item) => <a href={item.file_url} key={item.id}><FileText /> <span>{item.original_filename} • {item.imported_count} novo(s), {item.duplicate_count} duplicado(s)</span><small>SHA {item.content_sha256.slice(0, 12)}…</small></a>)}</details>}
            {card.cumulative && card.competencies?.length > 0 && <details className="routine-cumulative-history"><summary>Ver histórico por competência e créditos</summary><div className="routine-cumulative-history-grid"><div><strong>Competências</strong>{card.competencies.map((item) => <span key={item.reference_month}>{month(item.reference_month)} • esperado {money(item.expected_value)}{Number(item.adjustment_value || 0) > 0.01 ? ` • ajuste ${money(item.adjustment_value)}` : ""}</span>)}</div><div><strong>Créditos do portal</strong>{card.credit_history.map((item, index) => <span key={`${item.date}-${item.document || index}`}>{d(item.date)} • {money(item.value)}{item.document ? ` • NF ${item.document}` : ""}</span>)}</div></div></details>}
            <div className="routine-card-actions"><Link className="secondary link-button" to={card.queue_url}><Eye /> Abrir conciliação</Link></div>
          </article>)}</div> : <Empty text="Nenhuma competência neste grupo com os filtros selecionados." />}
        </section>;
      })}
      {isAdmin && data.raizen_receipts.length > 0 && <section className="routine-section"><div className="section-title"><div><h3>TEDs Raízen que exigem rateio</h3><p>Use somente quando um comprovante cobrir mais de uma competência.</p></div><Badge status="review_required">{data.raizen_receipts.length}</Badge></div><div className="routine-receipt-list">{data.raizen_receipts.map((receipt) => <RaizenReceiptAllocation key={receipt.id} receipt={receipt} onSaved={setData} />)}</div></section>}
      <section className="routine-section"><div className="section-title"><div><h3>Histórico de arquivos importados</h3><p>Originais preservados com hash, período e usuário responsável.</p></div></div>{data.imports.length ? <div className="routine-history">{data.imports.map((item) => <a href={item.file_url} key={item.id}><div className="avatar"><FileText /></div><div><strong>{item.original_filename}</strong><span>{item.unit_code} • {d(item.period_start)} a {d(item.period_end)} • {item.imported_count} novo(s) • {item.duplicate_count} duplicado(s)</span><small>Importado por {item.uploaded_by || "usuário não identificado"} • SHA {item.content_sha256.slice(0, 12)}…</small></div><Download /></a>)}</div> : <Empty text="Nenhum arquivo importado para os filtros selecionados." />}</section>
    </>}
  </>;
}

function Reports({ user }) {
  const [rows, setRows] = useState(null);
  const [reference, setReference] = useState(isoMonth());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = async () => {
    setError("");
    try {
      setRows(await get("/reports/monthly"));
    } catch (err) {
      setError(err.message);
      setRows([]);
    }
  };
  useEffect(() => {
    load();
  }, []);
  async function create(send) {
    setBusy(true);
    setError("");
    try {
      await post("/reports/monthly", {
        reference_month: `${reference}-01`,
        send,
      });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader
        eyebrow="PRESTAÇÃO DE CONTAS"
        title="Relatórios mensais"
        subtitle="PDF consolidado, histórico de tentativas e rastreabilidade do envio."
        actions={
          user.role === "admin" && (
            <div className="report-actions">
              <input
                type="month"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
              />
              <button
                className="secondary"
                disabled={busy}
                onClick={() => create(false)}
              >
                <FileBarChart /> Gerar PDF
              </button>
              <button
                className="primary"
                disabled={busy}
                onClick={() => create(true)}
              >
                <Send /> Gerar e enviar
              </button>
            </div>
          )
        }
      />
      {error && <div className="form-error">{error}</div>}
      {!rows ? (
        <Loading />
      ) : rows.length ? (
        <div className="panel table-panel">
          <div className="table-toolbar"><span>{rows.length} relatório(s) no histórico</span><ExcelExportButton filename="historico-de-relatorios" sheetName="Relatórios" rows={rows} columns={[{ label: "Competência", value: (row) => month(row.reference_month) }, { label: "Criado em", value: (row) => d(row.created_at) }, { label: "Status", value: (row) => row.status }, { label: "Tentativas", value: (row) => Number(row.attempts || 0) }, { label: "ID do provedor", value: (row) => row.provider_message_id || "" }, { label: "Detalhe", value: (row) => row.error_message || "" }]} /></div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Competência</th>
                  <th>Criado em</th>
                  <th>Status</th>
                  <th>Tentativas</th>
                  <th>ID do provedor</th>
                  <th>Detalhe</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <strong>{month(row.reference_month)}</strong>
                    </td>
                    <td>{d(row.created_at)}</td>
                    <td>
                      <Badge status={row.status} />
                    </td>
                    <td>{row.attempts}</td>
                    <td>{row.provider_message_id || "—"}</td>
                    <td className="error-cell">{row.error_message || "—"}</td>
                    <td>
                      {row.has_file && (
                        <a
                          className="icon-button"
                          href={`/api/reports/${row.id}/download`}
                          aria-label={`Baixar relatório de ${month(row.reference_month)}`}
                        >
                          <Download />
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot><tr><th colSpan="3">Total</th><th>{n(sumRows(rows, "attempts"))}</th><th colSpan="3" /></tr></tfoot>
            </table>
          </div>
        </div>
      ) : (
        <Empty text="Nenhum relatório gerado." />
      )}
    </>
  );
}

const configSets = {
  contracts: {
    title: "Contratos",
    fields: [
      ["unit_code", "Unidade"],
      ["company_code", "Companhia"],
      ["start_date", "Início", "date"],
      ["term_months", "Meses", "number"],
      ["total_liters", "Litros totais", "number"],
      ["upfront_total", "Antecipada total", "number"],
      ["upfront_per_liter", "Antecipada/L", "number"],
      ["postpaid_per_liter", "Postecipada/L", "number"],
      ["umbrella_group", "Grupo guarda-chuva"],
      ["status", "Status"],
    ],
  },
  rules: {
    title: "Regras de bonificação",
    fields: [
      ["unit_code", "Unidade"],
      ["company_code", "Companhia"],
      ["kind", "Modalidade"],
      ["effective_from", "Válida desde", "date"],
      ["effective_to", "Válida até", "date"],
      ["rate_per_liter", "Taxa/L", "number"],
      ["threshold_liters", "Limite litros", "number"],
      ["milestone_liters", "Marco litros", "number"],
      ["milestone_amount", "Valor do marco", "number"],
      ["period_months", "Período (meses)", "number"],
      ["due_day", "Dia limite", "number"],
      ["due_month_offset", "Deslocamento mês", "number"],
      ["applies_to", "Aplicação"],
    ],
  },
  aliases: {
    title: "Fornecedores e CNPJs",
    fields: [
      ["company_code", "Companhia"],
      ["unit_code", "Unidade"],
      ["cnpj", "CNPJ"],
      ["legal_name_pattern", "Nome contém"],
      ["effective_from", "Válido desde", "date"],
      ["effective_to", "Válido até", "date"],
    ],
  },
};

function ConfigSection({ type, rows, reload }) {
  const cfg = configSets[type];
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const requiredFields = {
    contracts: new Set(["unit_code", "company_code", "start_date", "term_months", "total_liters"]),
    rules: new Set(["unit_code", "company_code", "kind", "effective_from"]),
    aliases: new Set(["company_code", "effective_from"]),
  }[type];
  function start(row = {}) {
    const initial = {};
    cfg.fields.forEach(([key]) => (initial[key] = row[key] ?? ""));
    setError("");
    setEditing({ id: row.id, active: row.active ?? true, ...initial });
  }
  async function save(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const payload = {};
    cfg.fields.forEach(([key, , kind]) => {
      let value = editing[key];
      if (value === "") value = null;
      else if (kind === "number") value = Number(value);
      payload[key] = value;
    });
    if (type === "contracts") {
      payload.upfront_total ??= 0;
      payload.upfront_per_liter ??= 0;
      payload.postpaid_per_liter ??= 0;
      if (!payload.status) payload.status = "active";
    }
    if (type === "rules") {
      payload.active = editing.active;
      payload.rate_per_liter ??= 0;
      payload.due_month_offset ??= 1;
      payload.applies_to ||= "all_fuel";
    }
    if (type === "aliases") payload.active = editing.active;
    try {
      await (editing.id
        ? patch(`/admin/${type}/${editing.id}`, payload)
        : post(`/admin/${type}`, payload));
      setEditing(null);
      reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function deactivate(row) {
    if (!window.confirm(`Inativar este registro de ${cfg.title.toLowerCase()}?`)) return;
    setError("");
    setBusy(true);
    try {
      await remove(`/admin/${type}/${row.id}`);
      if (editing?.id === row.id) setEditing(null);
      await reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="admin-section">
      <div className="panel-heading">
        <div>
          <h2>{cfg.title}</h2>
          <p>{rows.length} registros configurados</p>
        </div>
        <button className="secondary" onClick={() => start()}>
          <Plus /> Adicionar
        </button>
      </div>
      {editing && (
        <form className="config-form" onSubmit={save}>
          {cfg.fields.map(([key, label, kind]) => (
            <label key={key}>
              {label}
              <input
                type={kind || "text"}
                value={editing[key]}
                step={kind === "number" ? "any" : undefined}
                required={requiredFields.has(key)}
                onChange={(e) =>
                  setEditing({ ...editing, [key]: e.target.value })
                }
              />
            </label>
          ))}
          <div className="form-buttons">
            <button
              type="button"
              className="ghost"
              onClick={() => setEditing(null)}
            >
              Cancelar
            </button>
            <button className="primary" disabled={busy}>{busy ? "Salvando..." : "Salvar"}</button>
          </div>
          {error && <div className="form-error">{error}</div>}
        </form>
      )}
      {!editing && error && <div className="form-error">{error}</div>}
      <div className="compact-list">
        {rows.map((row) => (
          <div className="config-list-row" key={row.id}>
            <button
              type="button"
              className="config-list-edit"
              aria-label={`Editar ${type} ${row.id}`}
              onClick={() => start(row)}
            >
              <span className="unit-chip">{row.unit_code || "GERAL"}</span>
              <div>
                <strong>
                  {type === "contracts"
                    ? `${row.company_code} • ${n(row.total_liters)} L`
                    : type === "rules"
                      ? ruleLabels[row.kind] || row.kind
                      : `${row.company_code} • ${row.cnpj || row.legal_name_pattern}`}
                </strong>
                <small>
                  {type === "contracts"
                    ? `${d(row.start_date)} — ${d(row.end_date)}`
                    : `Desde ${d(row.effective_from)}`}
                </small>
              </div>
              <Pencil />
            </button>
            <button
              type="button"
              className="icon-button danger"
              aria-label={`Inativar ${type} ${row.id}`}
              disabled={busy}
              onClick={() => deactivate(row)}
            >
              <X />
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function PortalIpirangaPanel({ data, onUpload, uploading, error, onPortalChange }) {
  const [savingId, setSavingId] = useState("");
  const [actionError, setActionError] = useState("");
  const [drafts, setDrafts] = useState({});

  useEffect(() => {
    if (!data?.supplemental?.events) return;
    setDrafts(
      Object.fromEntries(
        data.supplemental.events.map((event) => [
          event.id,
          {
            classification: event.review?.classification || "",
            notes: event.review?.notes || "",
          },
        ]),
      ),
    );
  }, [data]);

  if (!data) return <Loading />;

  const supplementalEvents = data.supplemental?.events || [];
  const classificationCounts = data.supplemental?.classification_counts || {};
  const classificationSummary = [
    "pending",
    "postpaid_regularization",
    "commercial_credit",
    "price_difference",
    "upfront",
    "other",
  ];

  const getDraft = (event) =>
    drafts[event.id] || {
      classification: event.review?.classification || "",
      notes: event.review?.notes || "",
    };

  const setDraftValue = (eventId, key, value) =>
    setDrafts((current) => ({
      ...current,
      [eventId]: {
        ...(current[eventId] || {}),
        [key]: value,
      },
    }));

  const resetDraft = (event) =>
    setDrafts((current) => ({
      ...current,
      [event.id]: {
        classification: event.review?.classification || "",
        notes: event.review?.notes || "",
      },
    }));

  async function saveSupplementalClassification(event, draft) {
    setActionError("");
    setSavingId(event.id);
    try {
      const result = await post(`/portal-statements/ipiranga/events/${event.id}/classify`, {
        classification: draft.classification || null,
        notes: draft.notes.trim() || null,
      });
      onPortalChange?.(result.dashboard);
    } catch (err) {
      setActionError(err.message);
    } finally {
      setSavingId("");
    }
  }

  return (
    <div className="portal-panel">
      <div className="portal-intro">
        <div>
          <p className="eyebrow">FONTE PRIMÁRIA DA DISTRIBUIDORA</p>
          <h3>Portal Ipiranga • unidade 001</h3>
          <p>
            Competências de 26 a 25 pela emissão da NF. O ERP permanece
            somente leitura; os arquivos e a auditoria ficam no banco desta aplicação.
          </p>
        </div>
        <form className="portal-upload" onSubmit={onUpload}>
          <input type="hidden" name="unit_code" value="001" />
          <label>
            Extrato ou relatorio Ipiranga (.xls/.xlsx)
            <input
              name="file"
              type="file"
              accept=".xls,.xlsx,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              required
            />
          </label>
          <button className="primary" disabled={uploading}>
            <FileUp /> {uploading ? "Importando..." : "Importar arquivo"}
          </button>
        </form>
      </div>
      {error && <div className="form-error">{error}</div>}

      <div className="portal-kpis">
        <Kpi icon={Gauge} label="Postecipada esperada" value={money(data.postpaid.expected_value)} detail={`${n(data.cycles.reduce((sum, row) => sum + row.liters, 0))} L em ciclos fechados`} tone="blue" />
        <Kpi icon={WalletCards} label="No portal" value={money(data.postpaid.portal_value)} detail={`${data.postpaid.exact_match_count}/${data.postpaid.event_count} cadeias exatas`} tone="green" />
        <Kpi icon={AlertTriangle} label="Diferença vencida" value={money(data.postpaid.difference_value)} detail={`${data.postpaid.overdue_cycle_count} ciclo(s) com saldo`} tone="red" />
        <Kpi icon={ShieldCheck} label="Ciclos automáticos" value={n(data.postpaid.automatic_cycle_count)} detail="Somente portal + cadeia ERP exata" tone="purple" />
      </div>

      <section className="portal-book">
        <div className="section-title section-title-wrap">
          <div className="section-title-main"><WalletCards />
          <div><h3>Livro da bonificação postecipada</h3><p>Esperado, concedido no portal e diferença por ciclo contratual.</p></div></div>
          <ExcelExportButton filename="livro-ipiranga-001" sheetName="Livro Ipiranga" rows={data.cycles} columns={[{ label: "Ciclo", value: (row) => row.cycle_number }, { label: "Início", value: (row) => d(row.period_start) }, { label: "Fim", value: (row) => d(row.period_end) }, { label: "Notas", value: (row) => Number(row.purchase_count || 0) }, { label: "Litros", value: (row) => Number(row.liters || 0) }, { label: "Esperado", value: (row) => Number(row.expected_value || 0) }, { label: "Portal", value: (row) => Number(row.observed_value || 0) }, { label: "Diferença", value: (row) => Number(row.difference_value || 0) }, { label: "Status", value: (row) => row.status }]} />
        </div>
        <div className="portal-table-wrap">
          <table className="portal-table">
            <thead><tr><th>Ciclo</th><th>Período</th><th>Notas</th><th>Litros</th><th>Esperado</th><th>Portal</th><th>Diferença</th><th>Status</th></tr></thead>
            <tbody>
              {data.cycles.map((row) => (
                <tr key={row.cycle_number}>
                  <td>#{row.cycle_number}</td><td>{d(row.period_start)} a {d(row.period_end)}</td><td>{row.purchase_count}</td><td>{n(row.liters)} L</td>
                  <td>{money(row.expected_value)}</td><td>{money(row.observed_value)}</td>
                  <td className={Math.abs(row.difference_value) > 0.01 ? "negative" : ""}>{money(row.difference_value)}</td><td><Badge status={row.status} /></td>
                </tr>
              ))}
            </tbody>
            <tfoot><tr><th>Total</th><th /><th>{n(sumRows(data.cycles, "purchase_count"))}</th><th>{n(sumRows(data.cycles, "liters"), 3)} L</th><th>{money(sumRows(data.cycles, "expected_value"))}</th><th>{money(sumRows(data.cycles, "observed_value"))}</th><th>{money(sumRows(data.cycles, "difference_value"))}</th><th /></tr></tfoot>
          </table>
        </div>
      </section>

      <section className="portal-book">
        <div className="section-title">
          <Link2 />
          <div><h3>Cadeias documentais dos lançamentos</h3><p>Cada crédito mostra arquivo, desconto, título, pedido/nota e 6204 correspondente.</p></div>
        </div>
        <div className="portal-event-list">
          {[...data.events].reverse().map((event) => (
            <article className="portal-event" key={event.id}>
              <div className="portal-event-head">
                <div><strong>{d(event.portal_date)} • {money(event.value)}</strong><span>{event.files.map((file) => `${file.filename} (${file.row_label || `linha ${file.row_number}`})`).join(" • ")}</span></div>
                <Badge status={event.match_status} />
              </div>
              {event.chain ? (
                <div className="portal-chain-grid">
                  <div><span>Desconto MDCMP</span><strong>{d(event.chain.movement_date)}</strong><small>{event.chain.movement_key}</small></div>
                  <div><span>Título/boleto</span><strong>{event.chain.title_document}-{event.chain.title_sequence}</strong><small>Baixa {event.chain.payment_movement_key}</small></div>
                  <div><span>Pedido e nota</span><strong>NF {event.chain.invoice_number}</strong><small>Entrada {event.chain.purchase_entry_id} • emissão {d(event.chain.invoice_issue_date)}</small></div>
                  <div><span>Financeiro</span><strong>6204 • lançamento {event.chain.financial_launch_id}</strong><small>{event.chain.financial_history}</small></div>
                </div>
              ) : <div className="detail-alert warning"><AlertTriangle /> Cadeia ERP ainda não comprovada.</div>}
              <div className="portal-allocations">
                {event.allocations.map((allocation, index) => <span key={`${event.id}-${index}`}>{money(allocation.value)} → ciclo #{allocation.cycle_number} ({d(allocation.period_start)} a {d(allocation.period_end)})</span>)}
                {Math.abs(event.unallocated_value) > 0.01 && <span className="negative">Sem alocação: {money(event.unallocated_value)}</span>}
              </div>
              <small className="match-basis">{event.match_basis}</small>
            </article>
          ))}
          {!data.events.length && <Empty text="Importe os extratos Ipiranga para iniciar a conciliação." />}
        </div>
      </section>

      <section className="portal-book">
        <div className="section-title section-title-wrap">
          <div className="section-title-main">
            <FileText />
            <div>
              <h3>Relatório de créditos complementar</h3>
              <p>
                Os itens CU reforçam a postecipada quando fecham com a mesma data e valor.
                Os créditos CZ, CR, CD e P# ficam separados como evidência suplementar
                até termos uma regra automática aprovada para cada caso.
              </p>
            </div>
          </div>
          <a
            className="secondary link-button"
            href="/api/portal-statements/ipiranga/001/supplemental.csv"
          >
            <Download /> Exportar CSV
          </a>
        </div>
        {actionError && <div className="form-error">{actionError}</div>}
        <div className="upfront-summary supplemental-summary-grid">
          <div><span>Total suplementar</span><strong>{money(data.supplemental.total_value)}</strong></div>
          <div><span>Antes da 1ª postecipada</span><strong>{money(data.supplemental.pre_postpaid_value)}</strong></div>
          <div><span>Eventos</span><strong>{n(data.supplemental.event_count)}</strong></div>
          <div><span>Categorias</span><strong>{n(data.supplemental.category_count)}</strong></div>
          <div><span>Cadeias exatas</span><strong>{n(data.supplemental.exact_chain_count)}</strong></div>
          <div><span>Classificados</span><strong>{n(data.supplemental.classified_count)}</strong></div>
        </div>
        <div className="portal-classification-summary">
          {classificationSummary.map((key) => (
            <div key={key}>
              <span>{supplementalClassificationLabels[key] || key}</span>
              <strong>{n(classificationCounts[key] || 0)}</strong>
            </div>
          ))}
        </div>
        <div className="admin-list compact-list">
          {data.supplemental.categories.map((row) => (
            <div key={row.category}>
              <div className="avatar"><Clock3 /></div>
              <div>
                <strong>{row.category_label}</strong>
                <span>{n(row.event_count)} evento(s) • {money(row.value)} • {d(row.first_date)} a {d(row.last_date)}</span>
                <small>{n(row.exact_chain_count)} cadeia(s) exata(s) • {n(row.classified_count)} classificado(s) • {n(row.pending_count)} pendente(s)</small>
              </div>
              <Badge status={row.pending_count ? "review_required" : "confirmed"}>
                {row.pending_count ? "Revisão aberta" : "Classificado"}
              </Badge>
            </div>
          ))}
        </div>
        {supplementalEvents.length ? (
          <div className="portal-event-list supplemental-events">
            {[...supplementalEvents].reverse().map((event) => {
              const draft = getDraft(event);
              const isDirty =
                draft.classification !== (event.review?.classification || "") ||
                draft.notes !== (event.review?.notes || "");
              return (
                <article className="portal-event portal-event-muted" key={event.id}>
                  <div className="portal-event-head">
                    <div>
                      <strong>{d(event.portal_date)} • {money(event.value)}</strong>
                      <span>{event.files.map((file) => `${file.filename} (${file.import_category_label}${file.row_label ? ` • ${file.row_label}` : ""})`).join(" • ")}</span>
                    </div>
                    <div className="portal-event-badges">
                      <Badge status="informative">{event.category_label}</Badge>
                      <Badge status={event.match_status} />
                      <Badge status={event.review?.classification ? "accepted" : "pending"}>
                        {event.review?.classification_label || "Sem classificação"}
                      </Badge>
                      {event.before_first_postpaid && <Badge status="review_required">Antes da 1ª postecipada</Badge>}
                    </div>
                  </div>
                  <small className="match-basis">
                    {event.description || event.product || "Crédito suplementar do relatório financeiro Ipiranga"}
                    {event.reference ? ` • ref. ${event.reference}` : ""}
                  </small>
                  {event.suggestion?.classification && (
                    <div className="portal-hint-row">
                      <Badge status="proposed">Sugestão: {event.suggestion.classification_label}</Badge>
                      <small>
                        {event.suggestion.reason}
                        {event.suggestion.confidence ? ` • confiança ${event.suggestion.confidence}` : ""}
                      </small>
                    </div>
                  )}
                  {event.chain ? (
                    <div className="portal-chain-grid">
                      <div><span>Desconto ERP</span><strong>{d(event.chain.movement_date)}</strong><small>{event.chain.movement_key || "Sem chave"}</small></div>
                      <div><span>Título / boleto</span><strong>{event.chain.title_document ? `${event.chain.title_document}${event.chain.title_sequence ? `-${event.chain.title_sequence}` : ""}` : "Sem título"}</strong><small>Baixa {event.chain.payment_movement_key || "não localizada"}</small></div>
                      <div><span>Pedido e nota</span><strong>{event.chain.invoice_number ? `NF ${event.chain.invoice_number}` : "NF não localizada"}</strong><small>Entrada {event.chain.purchase_entry_id || "—"} • emissão {d(event.chain.invoice_issue_date)}</small></div>
                      <div><span>Financeiro</span><strong>{event.chain.financial_launch_id ? `6204 • lançamento ${event.chain.financial_launch_id}` : "Sem 6204 único"}</strong><small>{event.chain.financial_history || "Histórico não localizado"}</small></div>
                    </div>
                  ) : <div className="detail-alert warning"><AlertTriangle /> Cadeia ERP ainda não comprovada para esse crédito.</div>}
                  <small className="match-basis">{event.match_basis}</small>
                  <form
                    className="supplemental-form"
                    onSubmit={(submitEvent) => {
                      submitEvent.preventDefault();
                      saveSupplementalClassification(event, draft);
                    }}
                  >
                    <label>
                      Classificação gerencial
                      <select
                        value={draft.classification}
                        onChange={(inputEvent) => setDraftValue(event.id, "classification", inputEvent.target.value)}
                      >
                        <option value="">Sem classificação</option>
                        <option value="postpaid_regularization">Regularização da postecipada</option>
                        <option value="commercial_credit">Crédito comercial</option>
                        <option value="price_difference">Diferença de preço</option>
                        <option value="upfront">Antecipação</option>
                        <option value="other">Outros créditos</option>
                      </select>
                    </label>
                    <label className="notes">
                      Observação do gestor
                      <textarea
                        rows="3"
                        value={draft.notes}
                        onChange={(inputEvent) => setDraftValue(event.id, "notes", inputEvent.target.value)}
                        placeholder="Ex.: regularização do período jan-abr/2025, crédito comercial, ajuste de preço, etc."
                      />
                    </label>
                    <div className="supplemental-actions">
                      <div className="supplemental-review-meta">
                        {event.review?.reviewed_at ? (
                          <>
                            <CheckCircle2 />
                            <span>Revisado por {event.review.reviewed_by || "administrador"} em {d(event.review.reviewed_at)}</span>
                          </>
                        ) : (
                          <>
                            <Pencil />
                            <span>Aguardando classificação administrativa.</span>
                          </>
                        )}
                      </div>
                      <div className="supplemental-buttons">
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => resetDraft(event)}
                          disabled={!isDirty || savingId === event.id}
                        >
                          <RefreshCw /> Reverter
                        </button>
                        <button className="primary" disabled={savingId === event.id || !isDirty}>
                          <CheckCircle2 />
                          {savingId === event.id ? "Salvando..." : "Salvar classificação"}
                        </button>
                      </div>
                    </div>
                  </form>
                </article>
              );
            })}
          </div>
        ) : <Empty text="Nenhum crédito suplementar importado ainda." />}
      </section>

      <section className="portal-book">
        <div className="section-title"><CreditCard /><div><h3>Livro informativo da antecipação</h3><p>O total contratual é confirmado; a classificação individual permanece provável.</p></div></div>
        <div className="upfront-summary">
          <div><span>Contratada</span><strong>{money(data.upfront.contracted_value)}</strong></div><div><span>Possível utilização</span><strong>{money(data.upfront.probable_used_value)}</strong></div><div><span>Saldo estimado</span><strong>{money(data.upfront.estimated_balance_value)}</strong></div>
        </div>
        <p className="policy-reason"><AlertTriangle /> {data.upfront.reason}</p>
        <div className="admin-list compact-list">
          {data.upfront.candidates.map((row) => (
            <div key={row.movement_key}><div className="avatar"><CreditCard /></div><div><strong>{d(row.date)} • {money(row.value)} • título {row.document}</strong><span>NF {row.invoice_number} • entrada {row.purchase_entry_id} • lançamento {row.financial_launch_id}</span></div><Badge status="probable_upfront" /></div>
          ))}
        </div>
      </section>

      <section className="portal-book">
        <div className="section-title"><FileText /><div><h3>Arquivos importados</h3><p>Originais preservados por SHA-256 para auditoria e reprocessamento.</p></div></div>
        <div className="admin-list compact-list">
          {data.imports.map((row) => (
            <div key={row.id}><div className="avatar"><FileText /></div><div><strong>{row.original_filename} • {row.category_label}</strong><span>{d(row.period_start)} a {d(row.period_end)} • {row.imported_count} novo(s) • {row.duplicate_count} sobreposto(s) • SHA {row.content_sha256.slice(0, 12)}…</span></div><a className="secondary link-button" href={`/api/portal-statements/imports/${row.id}/file`}><Download /> Original</a></div>
          ))}
        </div>
      </section>
    </div>
  );
}

function PortalTexaco050Panel({ data, onUpload, uploading, error }) {
  return (
    <div className="portal-panel">
      <div className="portal-intro">
        <div>
          <p className="eyebrow">FONTE PRIMARIA DA DISTRIBUIDORA</p>
          <h3>Portal Ipiranga/Texaco - unidade 050</h3>
          <p>
            Cada Nota Propria e conferida contra NF, titulo e baixa do ERP. O credito comercial fica separado ate receber a memoria de calculo.
          </p>
        </div>
        <form className="portal-upload" onSubmit={onUpload}>
          <input type="hidden" name="unit_code" value="050" />
          <label>
            Extrato consolidado Texaco (.pdf)
            <input name="file" type="file" accept=".pdf,application/pdf" required />
          </label>
          <button className="primary" disabled={uploading}>
            <FileUp /> {uploading ? "Importando..." : "Importar extrato"}
          </button>
        </form>
      </div>
      {error && <div className="form-error">{error}</div>}
      <div className="portal-kpis">
        <Kpi icon={Gauge} label="Esperado pela regra" value={money(data.summary.expected_value)} detail={`Vigencia confirmada desde ${d(data.effective_from)}`} tone="blue" />
        <Kpi icon={WalletCards} label="Creditos no portal" value={money(data.summary.portal_value)} detail={`${n(data.summary.exact_event_count)}/${n(data.summary.event_count)} cadeias exatas`} tone="green" />
        <Kpi icon={ShieldCheck} label="Identificado no painel" value={money(data.summary.observed_value)} detail="Somente creditos vinculados a NF e baixa" tone="purple" />
        <Kpi icon={AlertTriangle} label="Credito comercial" value={money(data.supplemental.reduce((sum, row) => sum + row.value, 0))} detail="Informativo: nao compoe bonus S10" tone="orange" />
      </div>
      <section className="portal-book">
        <div className="section-title"><Link2 /><div><h3>Creditos por nota</h3><p>Prova documental: portal → NF → titulo → pagamento.</p></div></div>
        <div className="portal-event-list">
          {[...data.events].reverse().map((event) => (
            <article className="portal-event" key={event.id}>
              <div className="portal-event-head">
                <div><strong>{d(event.portal_date)} - {money(event.value)}</strong><span>{event.files.map((file) => `${file.filename} (${file.row_label || `linha ${file.row_number}`})`).join(" - ")}</span></div>
                <Badge status={event.match_status} />
              </div>
              {event.allocations.length ? <div className="portal-allocations">{event.allocations.map((row, index) => <span key={`${event.id}-${index}`}>NF {row.invoice_number || "-"} → titulo {row.title_document || "-"} → {money(row.expected_value)} {row.payment_date ? `(${d(row.payment_date)})` : ""}</span>)}</div> : <div className="detail-alert warning"><AlertTriangle /> Sem vinculo unico com NF no ERP.</div>}
              <small className="match-basis">{event.match_basis}</small>
            </article>
          ))}
          {!data.events.length && <Empty text="Importe os extratos da unidade 050 para iniciar a conciliacao pelo portal." />}
        </div>
      </section>
      <section className="portal-book">
        <div className="section-title"><AlertTriangle /><div><h3>Creditos comerciais em analise</h3><p>Ficam visiveis, mas nunca sao apropriados como S10 ou desconto contratual sem memoria de calculo.</p></div></div>
        <div className="portal-event-list">
          {data.supplemental.map((event) => <article className="portal-event portal-event-muted" key={event.id}><div className="portal-event-head"><div><strong>{d(event.portal_date)} - {money(event.value)}</strong><span>{event.description}</span></div><Badge status="review_required">Em analise</Badge></div><small className="match-basis">{event.match_basis}</small></article>)}
          {!data.supplemental.length && <Empty text="Nenhum credito comercial adicional no extrato importado." />}
        </div>
      </section>
      <section className="portal-book">
        <div className="section-title"><FileText /><div><h3>Arquivos importados</h3><p>Originais preservados com hash e periodo para auditoria.</p></div></div>
        <div className="admin-list compact-list">
          {data.imports.map((row) => <div key={row.id}><div className="avatar"><FileText /></div><div><strong>{row.original_filename}</strong><span>{d(row.period_start)} a {d(row.period_end)} - {row.imported_count} novo(s) - {row.duplicate_count} sobreposto(s)</span></div><a className="secondary link-button" href={`/api/portal-statements/imports/${row.id}/file`}><Download /> Original</a></div>)}
        </div>
      </section>
    </div>
  );
}

function FreightRatesAdmin({ rows, reload, carrierRows = [] }) {
  const [editing, setEditing] = useState(null);
  const [units, setUnits] = useState([]);
  const [discoveredCarriers, setDiscoveredCarriers] = useState(carrierRows);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  useEffect(() => {
    get("/units").then(setUnits).catch(() => {});
    if (!carrierRows.length) {
      get("/admin/freight-carriers").then(setDiscoveredCarriers).catch(() => {});
    }
  }, []);
  useEffect(() => {
    if (carrierRows.length) setDiscoveredCarriers(carrierRows);
  }, [carrierRows]);
  const carriers = useMemo(() => {
    const indexed = new Map();
    [...discoveredCarriers, ...rows].forEach((row) => {
      if (!row.carrier_cnpj) return;
      indexed.set(row.carrier_cnpj, {
        carrier_cnpj: row.carrier_cnpj,
        carrier_name: row.carrier_name || row.carrier_cnpj,
      });
    });
    return [...indexed.values()].sort((a, b) =>
      a.carrier_name.localeCompare(b.carrier_name, "pt-BR"),
    );
  }, [discoveredCarriers, rows]);
  function start(row = {}) {
    setError("");
    setSuccess("");
    const defaultCarrier = row.carrier_cnpj
      ? { carrier_cnpj: row.carrier_cnpj, carrier_name: row.carrier_name }
      : carriers[0];
    setEditing({
      id: row.id,
      carrier_option: defaultCarrier?.carrier_cnpj || "manual",
      carrier_cnpj: defaultCarrier?.carrier_cnpj || "",
      carrier_name: defaultCarrier?.carrier_name || "",
      origin_cnpj: row.origin_cnpj || "",
      unit_code: row.unit_code || "",
      effective_from: row.effective_from || isoMonth() + "-01",
      effective_to: row.effective_to || "",
      rate_per_liter: row.rate_per_liter ?? "",
      active: row.active ?? true,
    });
  }
  async function save(event) {
    event.preventDefault();
    setError("");
    setSuccess("");
    const payload = {
      carrier_cnpj: editing.carrier_cnpj,
      carrier_name: editing.carrier_name || null,
      origin_cnpj: editing.origin_cnpj || null,
      unit_code: editing.unit_code || null,
      effective_from: editing.effective_from,
      effective_to: editing.effective_to || null,
      rate_per_liter: Number(editing.rate_per_liter),
      active: Boolean(editing.active),
    };
    try {
      const result = editing.id
        ? await patch(`/admin/freight-rates/${editing.id}`, payload)
        : await post("/admin/freight-rates", payload);
      setEditing(null);
      setSuccess(
        `Tarifa salva e ${n(result.reconciliations_rebuilt)} CT-es recalculados.`,
      );
      reload();
    } catch (err) {
      setError(err.message);
    }
  }
  async function deactivate(row) {
    if (!window.confirm("Desativar esta tarifa e recalcular o conciliador?")) return;
    try {
      const result = await patch(`/admin/freight-rates/${row.id}`, { ...row, active: false });
      setSuccess(
        `Tarifa desativada e ${n(result.reconciliations_rebuilt)} CT-es recalculados.`,
      );
      reload();
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <div className="freight-rate-admin">
      <div className="admin-section-lead">
        <div><strong>Tarifas independentes de frete</strong><span>Ao salvar, o conciliador recalcula automaticamente todos os CT-es afetados pela vigência.</span></div>
        <button className="primary" onClick={() => start()}><Plus /> Nova tarifa</button>
      </div>
      {error && <div className="form-error">{error}</div>}
      {success && <div className="success-banner"><CheckCircle2 /><span>{success}</span></div>}
      {editing && <form className="freight-rate-form" onSubmit={save}>
        <label>Transportadora<select value={editing.carrier_option} onChange={(event) => {
          const carrierOption = event.target.value;
          const selected = carriers.find((carrier) => carrier.carrier_cnpj === carrierOption);
          setEditing({
            ...editing,
            carrier_option: carrierOption,
            carrier_cnpj: selected?.carrier_cnpj || "",
            carrier_name: selected?.carrier_name || "",
          });
        }} required>
          {!carriers.length && <option value="manual">Informar manualmente</option>}
          {carriers.map((carrier) => <option value={carrier.carrier_cnpj} key={carrier.carrier_cnpj}>{carrier.carrier_name} • {carrier.carrier_cnpj}</option>)}
          <option value="manual">Outra transportadora...</option>
        </select></label>
        {editing.carrier_option === "manual" && <>
          <label>CNPJ do transportador<input value={editing.carrier_cnpj} onChange={(event) => setEditing({ ...editing, carrier_cnpj: event.target.value })} required /></label>
          <label>Nome do transportador<input value={editing.carrier_name} onChange={(event) => setEditing({ ...editing, carrier_name: event.target.value })} required /></label>
        </>}
        <label>CNPJ da origem <small>(opcional)</small><input value={editing.origin_cnpj} onChange={(event) => setEditing({ ...editing, origin_cnpj: event.target.value })} /></label>
        <label>Unidade <small>(opcional)</small><select value={editing.unit_code} onChange={(event) => setEditing({ ...editing, unit_code: event.target.value })}><option value="">Todas</option>{units.map((unit) => <option value={unit.code} key={unit.code}>{unit.code} • {unit.display_name}</option>)}</select></label>
        <label>Início da vigência<input type="date" value={editing.effective_from} onChange={(event) => setEditing({ ...editing, effective_from: event.target.value })} required /></label>
        <label>Fim da vigência <small>(opcional)</small><input type="date" value={editing.effective_to} onChange={(event) => setEditing({ ...editing, effective_to: event.target.value })} /></label>
        <label>Tarifa por litro (R$)<input type="number" min="0.000001" step="0.000001" value={editing.rate_per_liter} onChange={(event) => setEditing({ ...editing, rate_per_liter: event.target.value })} required /></label>
        <label className="checkbox-label"><input type="checkbox" checked={editing.active} onChange={(event) => setEditing({ ...editing, active: event.target.checked })} /> Tarifa ativa</label>
        <div className="freight-rate-form-actions"><button type="button" className="secondary" onClick={() => setEditing(null)}>Cancelar</button><button className="primary"><ShieldCheck /> Salvar tarifa</button></div>
      </form>}
      <div className="freight-rate-precedence">
        <ShieldCheck />
        <span>Prioridade automática: transportadora + origem + unidade, transportadora + unidade, transportadora + origem e, por último, tarifa global. Períodos sobrepostos no mesmo escopo são bloqueados.</span>
      </div>
      <div className="admin-list freight-rate-list">
        {rows.map((row) => <div key={row.id}>
          <div className="avatar"><Truck /></div>
          <div><strong>{row.carrier_name || row.carrier_cnpj}</strong><span>{row.origin_cnpj ? `Origem ${row.origin_cnpj}` : "Todas as origens"} • {row.unit_code ? `Unidade ${row.unit_code}` : "Todas as unidades"}</span>{row.origin_cnpj && <small className="table-subline"><FreightOriginSummary origin={row.origin} /></small>}<small>{d(row.effective_from)} a {row.effective_to ? d(row.effective_to) : "sem data final"}</small></div>
          <div className="freight-rate-amount"><strong>{rateMoney(row.rate_per_liter)} / L</strong><Badge status={row.active ? "confirmed" : "canceled"}>{row.active ? "Ativa" : "Inativa"}</Badge></div>
          <button className="icon-button" onClick={() => start(row)} title="Editar" aria-label={`Editar tarifa de ${row.carrier_name} vigente desde ${d(row.effective_from)}`}><Pencil /></button>
          {row.active && <button className="icon-button danger" onClick={() => deactivate(row)} title="Desativar" aria-label={`Desativar tarifa de ${row.carrier_name} vigente desde ${d(row.effective_from)}`}><X /></button>}
        </div>)}
        {!rows.length && <Empty text="Nenhuma tarifa cadastrada." />}
      </div>
    </div>
  );
}

function FreightRatesPage() {
  const [rows, setRows] = useState([]);
  const [carriers, setCarriers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = async () => {
    setError("");
    try {
      const [rateRows, carrierOptions] = await Promise.all([
        get("/admin/freight-rates"),
        get("/admin/freight-carriers"),
      ]);
      setRows(rateRows);
      setCarriers(carrierOptions);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);
  if (loading) return <Loading />;
  const activeRows = rows.filter((row) => row.active);
  const coveredCarriers = new Set(activeRows.map((row) => row.carrier_cnpj)).size;
  const openEnded = activeRows.filter((row) => !row.effective_to).length;
  return (
    <>
      <PageHeader
        eyebrow="GESTÃO DO CONCILIADOR"
        title="Tarifas de frete"
        subtitle="Cadastre valores por litro e vigências. Cada alteração recalcula automaticamente o período correspondente."
        actions={<Link className="secondary link-button" to="/fretes"><ArrowRight /> Abrir conciliação</Link>}
      />
      {error && <div className="form-error">{error}</div>}
      <section className="kpi-grid freight-rate-page-kpis">
        <Kpi icon={Gauge} label="Tarifas ativas" value={n(activeRows.length)} detail="Vigências usadas no cálculo" tone="green" />
        <Kpi icon={Truck} label="Transportadoras cobertas" value={n(coveredCarriers)} detail={`${n(carriers.length)} encontradas no ERP`} tone="blue" />
        <Kpi icon={Clock3} label="Sem data final" value={n(openEnded)} detail="Continuam válidas até nova alteração" tone="purple" />
      </section>
      <section className="panel freight-rate-page-panel">
        <FreightRatesAdmin rows={rows} carrierRows={carriers} reload={load} />
      </section>
    </>
  );
}

function UnitSettings({ rows, reload }) {
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  const start = (row) => setEditing({ ...row, display_name: row.display_name || "", city: row.city || "", brand: row.brand || "" });
  async function save(event) {
    event.preventDefault();
    try {
      await patch(`/admin/units/${editing.code}`, { display_name: editing.display_name, city: editing.city || null, brand: editing.brand || null });
      setEditing(null); setError(""); reload();
    } catch (err) { setError(err.message); }
  }
  return <section className="admin-section"><div className="panel-heading"><div><h2>Nome comercial e identificação dos postos</h2><p>O nome exibido em toda a plataforma é editável e não é sobrescrito pela sincronização.</p></div></div>{error && <div className="form-error">{error}</div>}{editing && <form className="config-form" onSubmit={save}><label>Nome do posto<input value={editing.display_name} onChange={(event) => setEditing({ ...editing, display_name: event.target.value })} required /></label><label>Cidade<input value={editing.city} onChange={(event) => setEditing({ ...editing, city: event.target.value })} /></label><label>Bandeira<select value={editing.brand} onChange={(event) => setEditing({ ...editing, brand: event.target.value })}><option value="">Não informada</option><option value="IPIRANGA">Ipiranga</option><option value="BR">BR</option><option value="SHELL">Shell</option><option value="TEXACO">Texaco</option></select></label><div className="form-buttons"><button type="button" className="ghost" onClick={() => setEditing(null)}>Cancelar</button><button className="primary">Salvar</button></div></form>}<div className="compact-list">{rows.map((row) => <button type="button" key={row.code} onClick={() => start(row)}><span className="unit-chip">{row.code}</span><div><strong>{unitName(row)}</strong><small>{row.city || "Cidade não informada"} • {row.brand || "Bandeira não informada"}</small></div><BrandLogo brand={row.brand} /><Pencil /></button>)}</div></section>;
}

export function Administration() {
  const [tab, setTab] = useState("users");
  const [data, setData] = useState({
    users: [],
    recipients: [],
    contracts: [],
    rules: [],
    aliases: [],
    units: [],
    freightRates: [],
    freightCarriers: [],
    sync: [],
    portal: null,
    portalTexaco: null,
  });
  const [portalUnit, setPortalUnit] = useState("001");
  const [resourceState, setResourceState] = useState(() =>
    Object.fromEntries(
      ["users", "recipients", "contracts", "rules", "aliases", "units", "freightRates", "sync", "portal", "portalTexaco"].map(
        (key) => [key, { loading: false, loaded: false, error: "" }],
      ),
    ),
  );
  const [error, setError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [portalError, setPortalError] = useState("");
  const [editingUser, setEditingUser] = useState(null);
  const [editingRecipient, setEditingRecipient] = useState(null);
  const activeResourceKey = tab === "portal" && portalUnit === "050" ? "portalTexaco" : tab;

  async function loadResource(key, force = false) {
    if (!force && (resourceState[key]?.loading || resourceState[key]?.loaded)) return;
    setResourceState((current) => ({
      ...current,
      [key]: { ...current[key], loading: true, error: "" },
    }));
    try {
      let result;
      if (key === "units") result = await get("/units");
      else if (key === "portal") result = await get("/portal-statements/ipiranga/001");
      else if (key === "portalTexaco") result = await get("/portal-statements/texaco/050");
      else if (key === "freightRates") {
        const [rates, carriers] = await Promise.all([
          get("/admin/freight-rates"),
          get("/admin/freight-carriers"),
        ]);
        result = rates;
        setData((current) => ({ ...current, freightCarriers: carriers }));
      } else {
        const endpoint = {
          users: "users",
          recipients: "recipients",
          contracts: "contracts",
          rules: "rules",
          aliases: "aliases",
          sync: "sync",
        }[key];
        result = await get(`/admin/${endpoint}`);
      }
      setData((current) => ({ ...current, [key]: result }));
      setResourceState((current) => ({
        ...current,
        [key]: { loading: false, loaded: true, error: "" },
      }));
    } catch (err) {
      setResourceState((current) => ({
        ...current,
        [key]: { loading: false, loaded: false, error: err.message },
      }));
    }
  }
  const load = () => loadResource(activeResourceKey, true);
  useEffect(() => {
    loadResource(activeResourceKey);
  }, [activeResourceKey]);
  async function saveUser(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const payload = Object.fromEntries(f);
    payload.active = editingUser?.active ?? true;
    if (!payload.password) delete payload.password;
    setActionBusy(true);
    setError("");
    try {
      await (editingUser
        ? patch(`/admin/users/${editingUser.id}`, payload)
        : post("/admin/users", payload));
      setEditingUser(null);
      form.reset();
      await loadResource("users", true);
    } catch (err) {
      setError(err.message);
    } finally {
      setActionBusy(false);
    }
  }
  async function saveRecipient(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const payload = { ...Object.fromEntries(f), active: editingRecipient?.active ?? true };
    setActionBusy(true);
    setError("");
    try {
      await (editingRecipient
        ? patch(`/admin/recipients/${editingRecipient.id}`, payload)
        : post("/admin/recipients", payload));
      setEditingRecipient(null);
      form.reset();
      await loadResource("recipients", true);
    } catch (err) {
      setError(err.message);
    } finally {
      setActionBusy(false);
    }
  }
  async function sync(kind = "incremental") {
    setActionBusy(true);
    setError("");
    try {
      await post(`/admin/sync?kind=${kind}`);
      await loadResource("sync", true);
    } catch (err) {
      setError(err.message);
    } finally {
      setActionBusy(false);
    }
  }
  async function uploadPortal(event) {
    event.preventDefault();
    setPortalError("");
    setUploading(true);
    const form = event.currentTarget;
    try {
      const formData = new FormData(form);
      const unitCode = String(formData.get("unit_code") || "001").padStart(3, "0");
      const result = await upload("/portal-statements/ipiranga", formData);
      setData((current) => ({
        ...current,
        [unitCode === "050" ? "portalTexaco" : "portal"]: result.dashboard,
      }));
      form.reset();
    } catch (err) {
      setPortalError(err.message);
    } finally {
      setUploading(false);
    }
  }
  return (
    <>
      <PageHeader
        eyebrow="ACESSO ADMINISTRATIVO"
        title="Administração"
        subtitle="Usuários, regras, destinatários e integração com o ERP."
      />
      {error && <div className="form-error">{error}</div>}
      <div className="tabs">
        {[
          ["users", "Usuários", Users],
          ["recipients", "Destinatários", Send],
          ["contracts", "Contratos", ClipboardCheck],
          ["units", "Postos", Building2],
          ["rules", "Bonificações", WalletCards],
          ["aliases", "Fornecedores", Building2],
          ["freightRates", "Tarifas de frete", Truck],
          ["portal", "Portal Ipiranga", FileUp],
          ["sync", "Sincronização", Database],
        ].map(([key, label, Icon]) => (
          <button
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
            key={key}
          >
            <Icon />
            {label}
          </button>
        ))}
      </div>
      <div className="panel admin-panel">
        {resourceState[activeResourceKey]?.loading ? (
          <Loading />
        ) : resourceState[activeResourceKey]?.error ? (
          <LoadFailure
            message={resourceState[activeResourceKey].error}
            onRetry={() => loadResource(activeResourceKey, true)}
          />
        ) : (
          <>
        {tab === "users" && (
          <>
            <form
              className="inline-form"
              key={editingUser?.id || "new-user"}
              onSubmit={saveUser}
            >
              <label>
                Nome
                <input name="full_name" defaultValue={editingUser?.full_name || ""} required />
              </label>
              <label>
                E-mail
                <input name="email" type="email" defaultValue={editingUser?.email || ""} required />
              </label>
              <label>
                Perfil
                <select name="role" defaultValue={editingUser?.role || "viewer"}>
                  <option value="viewer">Visualizador</option>
                  <option value="admin">Administrador</option>
                </select>
              </label>
              <label>
                {editingUser ? "Nova senha (opcional)" : "Senha inicial"}
                <input
                  name="password"
                  type="password"
                  minLength="10"
                  required={!editingUser}
                />
              </label>
              {editingUser && (
                <button type="button" className="ghost" onClick={() => setEditingUser(null)}>
                  Cancelar
                </button>
              )}
              <button className="primary" disabled={actionBusy}>
                {editingUser ? <Pencil /> : <Plus />}
                {editingUser ? "Salvar usuário" : "Criar usuário"}
              </button>
            </form>
            <div className="admin-list">
              {data.users.map((row) => (
                <div key={row.id}>
                  <div className="avatar">{row.full_name[0]}</div>
                  <div>
                    <strong>{row.full_name}</strong>
                    <span>{row.email}</span>
                  </div>
                  <Badge status={row.active ? "confirmed" : "overdue"}>
                    {row.role === "admin" ? "Admin" : "Visualizador"}
                  </Badge>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Editar ${row.full_name}`}
                    onClick={() => { setError(""); setEditingUser(row); }}
                  >
                    <Pencil />
                  </button>
                  <button
                    type="button"
                    className="icon-button danger"
                    aria-label={`Inativar ${row.full_name}`}
                    disabled={actionBusy}
                    onClick={async () => {
                      if (!window.confirm(`Inativar ${row.full_name}?`)) return;
                      setActionBusy(true);
                      setError("");
                      try {
                        await remove(`/admin/users/${row.id}`);
                        if (editingUser?.id === row.id) setEditingUser(null);
                        await loadResource("users", true);
                      } catch (err) {
                        setError(err.message);
                      } finally {
                        setActionBusy(false);
                      }
                    }}
                  >
                    <X />
                  </button>
                </div>
              ))}
            </div>
          </>
        )}
        {tab === "recipients" && (
          <>
            <form
              className="inline-form"
              key={editingRecipient?.id || "new-recipient"}
              onSubmit={saveRecipient}
            >
              <label>
                Nome
                <input name="name" defaultValue={editingRecipient?.name || ""} required />
              </label>
              <label>
                E-mail
                <input name="email" type="email" defaultValue={editingRecipient?.email || ""} required />
              </label>
              {editingRecipient && (
                <button type="button" className="ghost" onClick={() => setEditingRecipient(null)}>
                  Cancelar
                </button>
              )}
              <button className="primary" disabled={actionBusy}>
                {editingRecipient ? <Pencil /> : <Plus />}
                {editingRecipient ? "Salvar destinatário" : "Adicionar"}
              </button>
            </form>
            <div className="admin-list">
              {data.recipients.map((row) => (
                <div key={row.id}>
                  <div className="avatar">
                    <Send />
                  </div>
                  <div>
                    <strong>{row.name}</strong>
                    <span>{row.email}</span>
                  </div>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Editar ${row.name}`}
                    onClick={() => { setError(""); setEditingRecipient(row); }}
                  >
                    <Pencil />
                  </button>
                  <button
                    className="icon-button danger"
                    aria-label={`Remover ${row.name}`}
                    onClick={async () => {
                      setActionBusy(true);
                      setError("");
                      try {
                        if (!window.confirm(`Remover ${row.name}?`)) return;
                        await remove(`/admin/recipients/${row.id}`);
                        if (editingRecipient?.id === row.id) setEditingRecipient(null);
                        await loadResource("recipients", true);
                      } catch (err) {
                        setError(err.message);
                      } finally {
                        setActionBusy(false);
                      }
                    }}
                    disabled={actionBusy}
                  >
                    <X />
                  </button>
                </div>
              ))}
            </div>
          </>
        )}
        {["contracts", "rules", "aliases"].includes(tab) && (
          <ConfigSection type={tab} rows={data[tab]} reload={load} />
        )}
        {tab === "units" && <UnitSettings rows={data.units} reload={load} />}
        {tab === "freightRates" && (
          <FreightRatesAdmin
            rows={data.freightRates}
            carrierRows={data.freightCarriers}
            reload={load}
          />
        )}
        {tab === "sync" && (
          <>
            <div className="sync-actions">
              <div>
                <Database />
                <div>
                  <strong>Snapshot do ERP</strong>
                  <span>
                    O worker acessa o SQL Server em modo somente leitura.
                  </span>
                </div>
              </div>
              <button className="secondary" disabled={actionBusy} onClick={() => sync("incremental")}>
                <RefreshCw /> Sincronizar agora
              </button>
              <button
                className="primary"
                disabled={actionBusy}
                onClick={() =>
                  window.confirm(
                    "O backfill completo pode demorar. Continuar?",
                  ) && sync("full")
                }
              >
                <Database /> Backfill completo
              </button>
            </div>
            <div className="admin-list">
              {data.sync.map((row) => (
                <div key={row.id}>
                  <div className="avatar">
                    <Database />
                  </div>
                  <div>
                    <strong>
                      {row.kind === "full"
                        ? "Backfill completo"
                        : "Sincronização incremental"}
                    </strong>
                    <span>
                      {d(row.created_at)} • {row.rows_processed} linhas
                    </span>
                    {row.error_message && (
                      <small className="negative">{row.error_message}</small>
                    )}
                  </div>
                  <Badge
                    status={
                      row.status === "success"
                        ? "confirmed"
                        : row.status === "failed"
                          ? "overdue"
                          : "pending"
                    }
                  >
                    {row.status}
                  </Badge>
                </div>
              ))}
            </div>
          </>
        )}
        {tab === "portal" && (
          <>
            <div className="tabs">
              <button className={portalUnit === "001" ? "active" : ""} onClick={() => { setPortalUnit("001"); setPortalError(""); }}>Ipiranga - 001</button>
              <button className={portalUnit === "050" ? "active" : ""} onClick={() => { setPortalUnit("050"); setPortalError(""); }}>Texaco - 050</button>
            </div>
            {portalUnit === "001" ? (
              <PortalIpirangaPanel
                data={data.portal}
                onUpload={uploadPortal}
                uploading={uploading}
                error={portalError}
                onPortalChange={(portal) =>
                  setData((current) => ({ ...current, portal }))
                }
              />
            ) : (
              <PortalTexaco050Panel
                data={data.portalTexaco}
                onUpload={uploadPortal}
                uploading={uploading}
                error={portalError}
              />
            )}
          </>
        )}
          </>
        )}
      </div>
    </>
  );
}

function ChangePassword({ onChanged }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(e) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (f.get("new_password") !== f.get("confirm")) {
      setError("As senhas não coincidem.");
      return;
    }
    setBusy(true);
    try {
      await post("/auth/change-password", {
        current_password: f.get("current_password"),
        new_password: f.get("new_password"),
      });
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="password-page">
      <form className="password-card" onSubmit={save}>
        <img src="/logo-gbi.png" />
        <p className="eyebrow">PRIMEIRO ACESSO</p>
        <h1>Defina uma nova senha</h1>
        <p>Por segurança, troque a senha temporária antes de continuar.</p>
        {error && <div className="form-error">{error}</div>}
        <label>
          Senha atual
          <input type="password" name="current_password" required />
        </label>
        <label>
          Nova senha
          <input type="password" name="new_password" minLength="10" required />
        </label>
        <label>
          Confirme a nova senha
          <input type="password" name="confirm" minLength="10" required />
        </label>
        <button className="primary wide" disabled={busy}>
          Salvar e continuar
        </button>
      </form>
    </main>
  );
}

class AppErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("Falha ao renderizar a rota", error, info);
  }
  render() {
    if (this.state.error)
      return (
        <div className="route-error">
          <AlertTriangle />
          <h2>Não foi possível abrir esta tela</h2>
          <p>
            {this.state.error.message ||
              "Ocorreu um erro inesperado de renderização."}
          </p>
          <button className="primary" onClick={() => window.location.reload()}>
            <RefreshCw /> Recarregar tela
          </button>
        </div>
      );
    return this.props.children;
  }
}

function App() {
  const [user, setUser] = useState(undefined);
  const navigate = useNavigate();
  const location = useLocation();
  const loadUser = () =>
    get("/auth/me")
      .then(setUser)
      .catch(() => setUser(null));
  useEffect(() => {
    loadUser();
    const handler = () => setUser(null);
    window.addEventListener("contracts:unauthorized", handler);
    return () => window.removeEventListener("contracts:unauthorized", handler);
  }, []);
  async function logout() {
    try {
      await post("/auth/logout");
    } finally {
      setUser(null);
      navigate("/");
    }
  }
  if (user === undefined)
    return (
      <div className="app-loading">
        <img src="/logo-gbi.png" />
        <RefreshCw className="spin" />
      </div>
    );
  if (!user) return <Login onLogin={setUser} />;
  if (user.must_change_password) return <ChangePassword onChanged={loadUser} />;
  return (
    <Layout user={user} onLogout={logout}>
      <AppErrorBoundary key={location.pathname}>
        <Routes location={location}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/contratos" element={<Contracts />} />
          <Route path="/unidades/:code" element={<UnitDetail />} />
          <Route path="/compras" element={<Purchases />} />
          <Route
            path="/conciliacoes"
            element={<Reconciliations user={user} />}
          />
          <Route
            path="/rotina-mensal"
            element={<MonthlyRoutine user={user} />}
          />
          <Route path="/fretes" element={<Freights user={user} />} />
          <Route
            path="/tarifas-frete"
            element={
              user.role === "admin" ? <FreightRatesPage /> : <Navigate to="/fretes" />
            }
          />
          <Route path="/relatorios" element={<Reports user={user} />} />
          <Route
            path="/administracao"
            element={
              user.role === "admin" ? <Administration /> : <Navigate to="/" />
            }
          />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </AppErrorBoundary>
    </Layout>
  );
}

export default App;
