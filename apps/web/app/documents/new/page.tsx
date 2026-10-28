"use client";
/** S4 新增支出／收入／預估／預收。資料：POST /documents/check（即時檢查）、POST /documents。
 *  支出先選情況（v0.6）：一般支出／預付・先付部分／折讓・退貨／代墊・代付（components/SpecialExpense.tsx）。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { useDirectory } from "@/components/directory";
import { useQuery } from "@/components/filters";
import { FieldErrors, Mark, Tag, useToast } from "@/components/ui";
import { AllowanceForm, OnBehalfForm, PrepayForm } from "@/components/SpecialExpense";
import { ApiError, apiGet, apiSend } from "@/lib/api";
import { money } from "@/lib/format";
import type { Counterparty, Doc, Finding, OpenPrepayment } from "@/lib/types";

type Kind = "payable" | "receivable" | "accrual" | "advance_receipt";
type Line = { period_key: string; store_id: string; category_code: string; amount: string };
const KIND_LABEL: Record<Kind, string> = { payable: "支出", receivable: "收入", accrual: "預估入帳", advance_receipt: "預收款" };
type Case = "general" | "prepay" | "allowance" | "on_behalf";
const CASES: { k: Case; title: string; sub: string; eg: string }[] = [
  { k: "general", title: "一般支出", sub: "收到發票或收據，照實記一筆應付", eg: "蔬果、清潔用品、水電、記帳費" },
  { k: "prepay", title: "預付・先付部分", sub: "發票還沒來就先付訂金", eg: "年菜食材訂金、設備頭期款" },
  { k: "allowance", title: "折讓・退貨", sub: "廠商開了折讓單，從原本那張應付扣掉", eg: "品質不符、數量短少、價格回饋" },
  { k: "on_behalf", title: "代墊・代付", sub: "費用是 A 的，錢是 B 先付的", eg: "博竑帳戶付了中正店的修繕" },
];

const today = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
/** 網址有指定月份（例如從月結或待辦點進來）就預設那個月的最後一天，否則用今天 */
const initialDate = (period: string) => {
  if (!period || period === today().slice(0, 7)) return today();
  const [y, m] = period.split("-").map(Number);
  return `${period}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
};
const toInt = (s: string) => (s.trim() === "" ? 0 : Math.round(Number(s.replace(/,/g, "")) || 0));

export default function NewEntryPage() {
  const q = useQuery();
  const toast = useToast();
  const { dir, categories, categoryName } = useDirectory();
  const [kind, setKind] = useState<Kind>("payable");
  const [kase, setKase] = useState<Case>((["prepay", "allowance", "on_behalf"].includes(q.get("case")) ? q.get("case") : "general") as Case);
  const [prepays, setPrepays] = useState<OpenPrepayment[]>([]);
  const [usePre, setUsePre] = useState<Record<string, string>>({});
  const [company, setCompany] = useState(q.company || "");
  const [cpName, setCpName] = useState("");
  const [cpMatch, setCpMatch] = useState<Counterparty | null>(null);
  const [cpOptions, setCpOptions] = useState<Counterparty[]>([]);
  const [docDate, setDocDate] = useState(() => initialDate(q.get("period")));
  const [invoiceNo, setInvoiceNo] = useState("");
  const [memo, setMemo] = useState("");
  const [total, setTotal] = useState("");
  const [tax, setTax] = useState<string | null>(null);
  const [method, setMethod] = useState<"pending" | "bank_paid" | "petty_cash">("pending");
  const [accrualMode, setAccrualMode] = useState<"estimate_reverse" | "provision">("estimate_reverse");
  const [delivery, setDelivery] = useState("");
  const [lifeMonths, setLifeMonths] = useState("");
  const [lines, setLines] = useState<Line[]>([{ period_key: docDate.slice(0, 7), store_id: q.store || "", category_code: "", amount: "" }]);
  const [linesTouched, setLinesTouched] = useState(false);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [err, setErr] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { if (!company && dir.companies[0]) setCompany(q.company || dir.companies[0].id); }, [dir.companies, company, q.company]);

  const hasInvoice = invoiceNo.trim() !== "";
  const totalN = toInt(total);
  const autoTax = hasInvoice && kind !== "accrual" ? totalN - Math.round(totalN / 1.05) : 0;
  const taxN = tax === null ? autoTax : toInt(tax);
  const isAdvance = kind === "advance_receipt";
  // 分公司與總公司是同一法人：門市清單包含同一法人底下的所有門市
  const root = (id: string) => dir.companies.find((c) => c.id === id)?.parent_company_id || id;
  const storesOf = dir.stores.filter((s) => company && root(s.company_id) === root(company));
  const natures = kind === "receivable" ? ["income", "balance"] : kind === "accrual" ? ["expense", "income"] : ["expense", "balance"];
  const catOptions = categories.filter((c) => c.is_active && natures.includes(c.nature));
  const isBalance = (code: string) => categories.find((c) => c.code === code)?.nature === "balance";

  // 單行時，明細金額跟著總額走（未稅）
  useEffect(() => {
    if (linesTouched || lines.length !== 1) return;
    setLines((ls) => [{ ...ls[0], amount: totalN ? String(totalN - taxN) : "", period_key: docDate.slice(0, 7),
      category_code: ls[0].category_code || cpMatch?.default_category_code || "" }]);
  }, [totalN, taxN, docDate, cpMatch, linesTouched, lines.length]);

  // 對象搜尋
  useEffect(() => {
    const t = setTimeout(async () => {
      if (!cpName.trim()) { setCpOptions([]); setCpMatch(null); return; }
      try {
        const r = await apiGet<{ items: Counterparty[] }>("/settings/counterparties", { q: cpName.trim() });
        setCpOptions(r.items.slice(0, 8));
        setCpMatch(r.items.find((c) => c.name === cpName.trim()) || null);
      } catch { /* 搜尋失敗不擋登打 */ }
    }, 250);
    return () => clearTimeout(t);
  }, [cpName]);

  // 一般支出：這家廠商有未沖抵的預付 → 顯示沖抵區塊（5.13）
  useEffect(() => {
    setPrepays([]); setUsePre({});
    if (kind !== "payable" || !cpMatch || !company) return;
    apiGet<{ items: OpenPrepayment[] }>(`/vendors/${cpMatch.id}/open-prepayments`, { company_id: company, before: docDate })
      .then((r) => { setPrepays(r.items); }).catch(() => {});
  }, [kind, cpMatch, company, docDate]);
  const preTotal = Object.values(usePre).reduce((a, v) => a + toInt(v), 0);

  const body = useMemo(() => ({
    doc_type: kind, company_id: company, doc_date: docDate, counterparty_name: cpName.trim() || undefined,
    invoice_no: invoiceNo.trim() || undefined, has_invoice: hasInvoice, memo: memo || undefined,
    settle_amount: kind === "accrual" ? undefined : totalN || undefined, tax_amount: kind === "accrual" ? 0 : taxN,
    settlement_method: method, accrual_mode: kind === "accrual" ? accrualMode : undefined,
    expected_delivery_date: isAdvance ? delivery || undefined : undefined,
    asset_life_months: lifeMonths ? toInt(lifeMonths) : undefined,
    lines: isAdvance ? [] : lines.filter((l) => l.category_code && l.amount !== "").map((l) => ({
      period_key: l.period_key, category_code: l.category_code, amount: toInt(l.amount), store_id: l.store_id || null })),
  }), [kind, company, docDate, cpName, invoiceNo, hasInvoice, memo, totalN, taxN, method, accrualMode, isAdvance, delivery, lifeMonths, lines]);

  // 即時檢查（防抖 400ms）
  const seq = useRef(0);
  useEffect(() => {
    if (!company) return;
    const my = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const r = await apiSend<{ findings: Finding[] }>("POST", "/documents/check", body);
        if (my === seq.current) setFindings(r.findings);
      } catch { /* 檢查失敗不擋 */ }
    }, 400);
    return () => clearTimeout(t);
  }, [body, company]);

  const lineSum = body.lines.reduce((a, l) => a + l.amount * (kind === "receivable" && isBalance(l.category_code) ? -1 : 1), 0);
  const expected = kind === "accrual" || isAdvance ? null : totalN - taxN;
  const balanced = expected === null || lineSum === expected;
  const split = findings.find((f) => f.code === "split_months");
  const suggestion = findings.find((f) => f.code === "category" && f.data?.category_code);
  const capex = body.lines.some((l) => l.category_code === "B01");

  function applySplit() {
    const periods: string[] = split!.data.periods;
    const net = totalN - taxN;
    const base = Math.floor(net / periods.length);
    const tmpl = lines[0];
    setLinesTouched(true);
    setLines(periods.map((p, i) => ({ ...tmpl, period_key: p, amount: String(i === 0 ? net - base * (periods.length - 1) : base) })));
  }

  async function save(next: boolean) {
    setSaving(true);
    setErr(null);
    try {
      const d = await apiSend<Doc>("POST", "/documents", body);
      let offNote = "";
      for (const [pid, v] of Object.entries(usePre)) {
        if (toInt(v) > 0) {
          try {
            await apiSend("POST", "/offsets", { kind: "prepayment", from_document_id: pid, to_document_id: d.id, amount: toInt(v) });
            offNote = `，已沖抵預付 ${money(preTotal)}`;
          } catch (e) { toast(`單據已入帳，但沖抵失敗：${(e as Error).message}（可到待確認處理）`, "error"); }
        }
      }
      toast(`已入帳：${d.counterparty?.name || KIND_LABEL[kind]} ${money(d.settle_amount ?? body.lines.reduce((a, l) => a + l.amount, 0))}${offNote}`);
      if (next) {
        setCpName(""); setInvoiceNo(""); setMemo(""); setTotal(""); setTax(null); setLifeMonths(""); setLinesTouched(false); setUsePre({});
        setLines([{ period_key: docDate.slice(0, 7), store_id: lines[0].store_id, category_code: "", amount: "" }]);
      } else {
        window.location.href = q.keep("/payables");
      }
    } catch (e) {
      setErr(e as ApiError);
    } finally {
      setSaving(false);
    }
  }

  const setLine = (i: number, patch: Partial<Line>) => { setLinesTouched(true); setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l))); };

  const special = kind === "payable" && kase !== "general";
  const useGeneral = (preset?: string) => {
    setKase("general"); setLinesTouched(true);
    if (preset) setLines((ls) => ls.map((l, i) => (i === 0 ? { ...l, category_code: preset } : l)));
  };
  return (
    <>
      <PageHeader crumb="帳務" title={special ? `新增${CASES.find((c) => c.k === kase)!.title}` : `新增${KIND_LABEL[kind]}`}
                  sub="沒有經過發票系統的帳：無發票進貨、零用金、固定支出、預估與預收；支出另有預付、折讓、代付。" />
      <div className="card pad stack" style={{ marginBottom: 16, gap: 12 }}>
        <div className="f"><span id="kind-l">類型</span>
          <div className="seg" role="group" aria-labelledby="kind-l" style={{ flexWrap: "wrap" }}>
            {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
              <button type="button" key={k} aria-pressed={kind === k} onClick={() => { setKind(k); setLinesTouched(false); if (k !== "payable") setKase("general"); }}>{KIND_LABEL[k]}</button>
            ))}
          </div>
        </div>
        {kind === "payable" && (
          <fieldset style={{ border: 0, margin: 0, padding: 0 }}>
            <legend style={{ fontSize: 13, fontWeight: 500, color: "var(--text-2)", marginBottom: 6 }}>這筆支出是哪一種？</legend>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 10 }}>
              {CASES.map((c) => (
                <button type="button" key={c.k} aria-pressed={kase === c.k} onClick={() => setKase(c.k)} className="card"
                        style={{ textAlign: "left", padding: 12, cursor: "pointer", font: "inherit", color: "inherit",
                                 border: kase === c.k ? "2px solid var(--accent)" : "1px solid var(--line)",
                                 background: kase === c.k ? "var(--accent-soft)" : "var(--surface)" }}>
                  <div style={{ fontWeight: 600 }}>{c.title}</div>
                  <div className="small" style={{ color: "var(--text-2)" }}>{c.sub}</div>
                  <div className="small">例：{c.eg}</div>
                </button>
              ))}
            </div>
          </fieldset>
        )}
        {special && (
          <label className="f" style={{ maxWidth: 360 }}><span>公司</span>
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              {dir.companies.map((c) => <option key={c.id} value={c.id}>{c.parent_company_id ? "　└ " : ""}{c.name}</option>)}
            </select></label>
        )}
      </div>
      {special && kase === "prepay" && <PrepayForm company={company} docDate={docDate} setDocDate={setDocDate} />}
      {special && kase === "allowance" && <AllowanceForm company={company} docDate={docDate} setDocDate={setDocDate} />}
      {special && kase === "on_behalf" && <OnBehalfForm company={company} docDate={docDate} setDocDate={setDocDate} onUseGeneral={useGeneral} />}
      {!special && <div className="row">
        <form className="card grow pad stack" style={{ gap: 18 }} aria-label="單據內容" onSubmit={(e) => { e.preventDefault(); save(true); }}>
          <div className="form-grid">
            <label className="f"><span>公司</span>
              <select value={company} onChange={(e) => setCompany(e.target.value)}>
                {dir.companies.map((c) => <option key={c.id} value={c.id}>{c.parent_company_id ? "　└ " : ""}{c.name}</option>)}
              </select>
            </label>
            <label className="f"><span>對象</span>
              <input value={cpName} list="cp-list" onChange={(e) => setCpName(e.target.value)} placeholder="廠商、客戶或其他對象" />
              <datalist id="cp-list">{cpOptions.map((c) => <option key={c.id} value={c.name} />)}</datalist>
              <span className="small">{cpMatch ? `預設類別：${cpMatch.default_category_code ? categoryName(cpMatch.default_category_code) : "未設定"}${cpMatch.is_intercompany ? "・集團內" : ""}`
                : cpName ? "新對象：儲存後會建立並列入待確認" : ""}</span>
            </label>
            <label className="f"><span>單據日期</span><input type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></label>
            {kind !== "accrual" && <label className="f"><span>發票號碼</span>
              <input value={invoiceNo} className="num" onChange={(e) => setInvoiceNo(e.target.value.toUpperCase())} placeholder="無發票請留空" />
              <span className="small">{hasInvoice ? "有發票：明細金額為未稅" : "無發票：金額即成本，不除 1.05"}</span></label>}
            <label className="f"><span>摘要</span><input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="例：7+8月管理費" /></label>
            {kind !== "accrual" && <label className="f"><span>{isAdvance ? "預收金額（含稅）" : "應付／應收金額（含稅）"}</span>
              <input className="amt" inputMode="numeric" value={total} onChange={(e) => setTotal(e.target.value)} /></label>}
            {kind !== "accrual" && !isAdvance && <label className="f"><span>稅額</span>
              <input className="amt" inputMode="numeric" value={tax === null ? String(autoTax) : tax} onChange={(e) => setTax(e.target.value)} />
              <span className="small">{tax === null ? "依 5% 自動計算" : <button type="button" className="linkbtn small" onClick={() => setTax(null)}>改回自動計算</button>}</span></label>}
            {kind === "accrual" && <label className="f"><span>預估模式</span>
              <select value={accrualMode} onChange={(e) => setAccrualMode(e.target.value as never)}>
                <option value="estimate_reverse">預估後沖回（帳單晚到）</option><option value="provision">分月預提（年終、三節）</option>
              </select></label>}
            {isAdvance && <label className="f"><span>預計交貨／兌換日</span><input type="date" value={delivery} onChange={(e) => setDelivery(e.target.value)} /></label>}
          </div>

          {(kind === "payable" || kind === "receivable") && (
            <div className="f"><span id="pay-l">付款方式</span>
              <div className="seg" role="group" aria-labelledby="pay-l" style={{ maxWidth: 560 }}>
                <button type="button" aria-pressed={method === "pending"} onClick={() => setMethod("pending")}>{kind === "payable" ? "待付款" : "待收款"}</button>
                <button type="button" aria-pressed={method === "bank_paid"} onClick={() => setMethod("bank_paid")}>{kind === "payable" ? "已從銀行付款" : "已收到匯款"}</button>
                {kind === "payable" && <button type="button" aria-pressed={method === "petty_cash"} onClick={() => setMethod("petty_cash")}>零用金</button>}
              </div>
              <span className="small">{method === "petty_cash" ? "零用金支付，存檔即視為已付，不會送到現金流系統。"
                : "會出現在現金流系統的未結清清單，核對到銀行交易後自動標為已結清。"}</span>
            </div>
          )}

          {!isAdvance && (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                <span className="f" style={{ fontSize: 13, fontWeight: 500 }}>明細（記在哪家店、哪個月、哪一類）</span>
                <button type="button" className="linkbtn small" onClick={() => { setLinesTouched(true); setLines((ls) => [...ls, { ...ls[ls.length - 1], amount: "" }]); }}>新增一行</button>
              </div>
              <div className="tbl-wrap">
                <table>
                  <thead><tr><th style={{ width: 130 }}>損益月份</th><th>門市</th><th>類別</th><th style={{ width: 150 }} className="r">金額（未稅）</th><th style={{ width: 44 }}><span className="sr">刪除</span></th></tr></thead>
                  <tbody>
                    {lines.map((l, i) => (
                      <tr key={i}>
                        <td><input type="month" aria-label={`第 ${i + 1} 行月份`} value={l.period_key} onChange={(e) => setLine(i, { period_key: e.target.value })} /></td>
                        <td><select style={{ minWidth: 170 }} aria-label={`第 ${i + 1} 行門市`} value={l.store_id} onChange={(e) => setLine(i, { store_id: e.target.value })}>
                          <option value="">公司層級（待分攤）</option>{storesOf.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></td>
                        <td><select aria-label={`第 ${i + 1} 行類別`} value={l.category_code} onChange={(e) => setLine(i, { category_code: e.target.value })}>
                          <option value="">選擇類別</option>{catOptions.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}{c.nature === "balance" ? "（不入損益）" : ""}</option>)}</select></td>
                        <td><input className="amt" inputMode="numeric" aria-label={`第 ${i + 1} 行金額`} value={l.amount} onChange={(e) => setLine(i, { amount: e.target.value })} /></td>
                        <td>{lines.length > 1 && <button type="button" className="btn sm" aria-label={`刪除第 ${i + 1} 行`} onClick={() => { setLinesTouched(true); setLines((ls) => ls.filter((_, j) => j !== i)); }}>✕</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {expected !== null && (
                <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, alignItems: "center", padding: "10px 8px 0", fontSize: 13, flexWrap: "wrap" }}>
                  <span>明細 <span className="num">{money(lineSum, { zeroDash: false })}</span> ＋ 稅額 <span className="num">{money(taxN, { zeroDash: false })}</span> ＝ <span className="num" style={{ fontWeight: 600 }}>{money(lineSum + taxN, { zeroDash: false })}</span></span>
                  {balanced ? <Tag kind="ok">與應付金額相符</Tag> : <Tag kind="neg">差 {money(expected - lineSum, { zeroDash: false })}</Tag>}
                </div>
              )}
            </div>
          )}

          {prepays.length > 0 && (
            <section className="ai stack" aria-label="未沖抵的預付款" style={{ gap: 8 }}>
              <div style={{ fontWeight: 600 }}>{cpMatch?.name} 有 {prepays.length} 筆預付款還沒沖抵，要用在這張發票嗎？</div>
              <div className="tbl-wrap"><table>
                <thead><tr><th>沖抵</th><th>預付日</th><th>說明</th><th className="r">可沖抵</th><th className="r">本次沖抵</th></tr></thead>
                <tbody>{prepays.map((p) => (
                  <tr key={p.document_id}>
                    <td><input type="checkbox" aria-label={`沖抵 ${p.doc_date} 預付款`} checked={p.document_id in usePre}
                               onChange={(e) => setUsePre((u) => { const n = { ...u }; if (e.target.checked) n[p.document_id] = String(Math.min(p.remaining, Math.max(0, totalN - preTotal))); else delete n[p.document_id]; return n; })} /></td>
                    <td className="num">{p.doc_date}</td><td>{p.memo || "—"}{!p.paid && <> <Tag kind="warn">尚未付款</Tag></>}</td>
                    <td className="num r">{money(p.remaining)}</td>
                    <td className="r"><input className="amt" style={{ width: 120 }} inputMode="numeric" aria-label="本次沖抵金額"
                               disabled={!(p.document_id in usePre)} value={usePre[p.document_id] ?? ""}
                               onChange={(e) => setUsePre((u) => ({ ...u, [p.document_id]: e.target.value }))} /></td>
                  </tr>))}</tbody>
              </table></div>
              {preTotal > 0 && <div className="small" style={{ textAlign: "right" }}>
                發票 <span className="num">{money(totalN)}</span> − 沖抵預付 <span className="num">{money(-preTotal, { zeroDash: false })}</span> ＝ 尚應付 <b className="num">{money(totalN - preTotal, { zeroDash: false })}</b></div>}
              <div className="small">可以只沖一部分（訂金涵蓋兩張發票時，分兩次沖）。不在這裡沖，存檔後待確認也會提醒。</div>
            </section>
          )}

          {capex && <label className="f" style={{ maxWidth: 260 }}><span>耐用年限（月）</span>
            <input className="amt" inputMode="numeric" value={lifeMonths} onChange={(e) => setLifeMonths(e.target.value)} placeholder="例：36" />
            <span className="small">資本支出不進損益，系統每月自動提列折舊</span></label>}

          <FieldErrors error={err} />
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", borderTop: "1px solid var(--line)", paddingTop: 16 }}>
            <button type="submit" className="btn pri" disabled={saving || !balanced}>儲存並新增下一筆</button>
            <button type="button" className="btn" disabled={saving || !balanced} onClick={() => save(false)}>儲存</button>
          </div>
        </form>

        <aside className="card aside pad" aria-label="即時檢查" aria-live="polite">
          <h2>即時檢查</h2>
          <div className="small" style={{ marginBottom: 4 }}>欄位變動後自動檢查</div>
          {findings.length === 0 && <p className="sub">填入公司與對象後開始檢查。</p>}
          {findings.map((f, i) => (
            <div key={i} className="chk" style={{ padding: "10px 0" }}>
              <Mark status={f.level === "error" ? "error" : f.level} />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600 }}>{f.message}</div>
                {f.code === "split_months" && <button type="button" className="linkbtn small" onClick={applySplit}>拆成 {f.data.periods.join("、")}</button>}
                {f.code === "category" && f.data?.category_code && <div className="small">{categoryName(f.data.category_code)}{f.data.reason ? `：${f.data.reason}` : ""}</div>}
              </div>
            </div>
          ))}
          {suggestion && lines[0] && !lines[0].category_code && (
            <button type="button" className="btn sm" onClick={() => setLine(0, { category_code: suggestion.data.category_code })}>採用建議類別</button>
          )}
        </aside>
      </div>}
    </>
  );
}
