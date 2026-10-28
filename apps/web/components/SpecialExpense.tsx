"use client";
/**
 * 新增支出的三種特殊情況（開發文件 v0.6：5.13 預付、5.14 折讓、5.15 代付；原型見 Design 畫布《新增支出情境畫面》）。
 * 資料：POST /prepayments、GET /documents/lookup、GET /documents/original-candidates、POST /allowances、POST /on-behalf
 */
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ApiError, apiGet, apiSend } from "@/lib/api";
import { money } from "@/lib/format";
import type { Doc, VendorHit } from "@/lib/types";
import { useDirectory } from "./directory";
import { FieldErrors, Tag, useToast } from "./ui";
import { VendorPicker } from "./VendorPicker";

const toInt = (s: string) => (s.trim() === "" ? 0 : Math.round(Number(s.replace(/,/g, "")) || 0));
const INVOICE_RE = /^[A-Z]{2}-?\d{8}$/;

function useStores(company: string) {
  const { dir } = useDirectory();
  const root = (id: string) => dir.companies.find((c) => c.id === id)?.parent_company_id || id;
  return dir.stores.filter((s) => company && root(s.company_id) === root(company));
}

function Seg<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: [T, string][]; onChange: (v: T) => void;
}) {
  return (
    <div className="f"><span>{label}</span>
      <div className="seg" role="group" aria-label={label} style={{ flexWrap: "wrap" }}>
        {options.map(([k, t]) => <button type="button" key={k} aria-pressed={value === k} onClick={() => onChange(k)}>{t}</button>)}
      </div>
    </div>
  );
}

function Effect({ title, rows }: { title: string; rows: { k: string; v: React.ReactNode; note?: string }[] }) {
  return (
    <aside className="card aside pad" aria-label={title} aria-live="polite">
      <h2>{title}</h2>
      <div className="stack" style={{ gap: 10, marginTop: 8 }}>
        {rows.map((r, i) => (
          <div key={i} style={{ borderBottom: "1px solid var(--line-2)", paddingBottom: 8 }}>
            <div className="small">{r.k}</div>
            <div>{r.v}</div>
            {r.note && <div className="small">{r.note}</div>}
          </div>
        ))}
      </div>
    </aside>
  );
}

// ================================================================ 預付款
export function PrepayForm({ company, docDate, setDocDate }: { company: string; docDate: string; setDocDate: (d: string) => void }) {
  const toast = useToast();
  const { categories, companyName } = useDirectory();
  const stores = useStores(company);
  const [vendor, setVendor] = useState<VendorHit | null>(null);
  const [store, setStore] = useState("");
  const [amount, setAmount] = useState("");
  const [orderTotal, setOrderTotal] = useState("");
  const [method, setMethod] = useState<"bank_paid" | "petty_cash" | "pending">("bank_paid");
  const [invPeriod, setInvPeriod] = useState(docDate.slice(0, 7));
  const [planned, setPlanned] = useState("");
  const [memo, setMemo] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const cats = categories.filter((c) => c.is_active && c.nature === "expense" || c.code === "B01");
  const noTax = vendor && !vendor.tax_id;
  const amt = toInt(amount), tot = toInt(orderTotal);

  useEffect(() => { if (vendor?.default_category_code && !planned) setPlanned(vendor.default_category_code); }, [vendor, planned]);

  async function save() {
    if (!vendor) return;
    setSaving(true); setErr(null);
    try {
      await apiSend<Doc>("POST", "/prepayments", {
        company_id: company, store_id: store || null, counterparty_id: vendor.id, doc_date: docDate, amount: amt,
        order_total: tot || null, settlement_method: method, expected_invoice_period: invPeriod || null,
        planned_category_code: planned || null, memo: memo || null });
      toast(`已建立預付單：${vendor.name} ${money(amt)}，發票來時會提醒沖抵`);
      setVendor(null); setAmount(""); setOrderTotal(""); setMemo("");
    } catch (e) { setErr(e as ApiError); } finally { setSaving(false); }
  }

  return (
    <div className="row">
      <form className="card grow pad stack" style={{ gap: 18 }} aria-label="預付單" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <div className="small">訂金不會有發票：付款時先記一張預付單（不進損益），交貨的全額發票進來時，系統依<b>賣方統編</b>找出這筆並提醒沖抵。
          已經有應付單、只是先付一部分的，請在「應付帳款」打開那張單據標記現金付款。</div>
        <div className="form-grid">
          <VendorPicker value={vendor} onChange={setVendor} companyId={company} required />
          <label className="f"><span>門市</span>
            <select value={store} onChange={(e) => setStore(e.target.value)}>
              <option value="">公司層級</option>{stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
          <label className="f"><span>付款日期</span><input type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></label>
          <label className="f"><span>預付金額（含稅）</span><input className="amt" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
          <label className="f"><span>整筆訂購總額（選填）</span><input className="amt" inputMode="numeric" value={orderTotal} onChange={(e) => setOrderTotal(e.target.value)} />
            <span className="small">{tot > amt && amt ? `待辦會顯示「還有 ${money(tot - amt)} 等發票」` : "填了之後待辦會提醒還有多少等發票"}</span></label>
          <label className="f"><span>預計收到發票</span><input type="month" value={invPeriod} onChange={(e) => setInvPeriod(e.target.value)} /></label>
          <label className="f"><span>預定類別（發票來時預設帶入）</span>
            <select value={planned} onChange={(e) => setPlanned(e.target.value)}>
              <option value="">未指定</option>{cats.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}</option>)}</select></label>
          <label className="f"><span>用途說明</span><input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="例：年菜豬腳、牛腱訂金" /></label>
        </div>
        <Seg label="怎麼付的" value={method} onChange={setMethod}
             options={[["bank_paid", "銀行已付（等現金流配對）"], ["petty_cash", "零用金現金"], ["pending", "還沒付"]]} />
        {noTax && (
          <div className="errbox" role="alert">
            {vendor!.no_tax_id ? "這家廠商沒有統編：發票來時不會自動比對，沖抵要由會計人工確認。"
              : <>這家廠商還沒有統編，預付單建不起來。請先到 <Link href="/vendor-master">廠商主檔</Link> 補統編，或勾選「沒有統編」。</>}
          </div>
        )}
        <FieldErrors error={err} />
        <div style={{ display: "flex", gap: 10, borderTop: "1px solid var(--line)", paddingTop: 16 }}>
          <button type="submit" className="btn pri" disabled={saving || !vendor || !amt || (!!noTax && !vendor?.no_tax_id)}>建立預付單</button>
        </div>
      </form>
      <Effect title="建立後會發生什麼" rows={[
        { k: `${companyName(company)}・本月損益`, v: <>成本 <span className="num">— 不變</span></>, note: "訂金不是費用，發票來了才算成本" },
        { k: "B 類餘額（不進損益）", v: <>B07 預付款{vendor ? `・${vendor.name}` : ""} <span className="num">+{money(amt || 0, { zeroDash: false })}</span></> },
        { k: "銀行核對", v: method === "bank_paid" ? "這筆銀行支出會對到這張預付單，不會變成「有付款沒發票」" : method === "petty_cash" ? "零用金付款，存檔即視為已付" : "會列在未付清單，付款後由銀行核對結清" },
        { k: "待辦提醒", v: `同一家廠商（統編 ${vendor?.tax_id || "—"}）的發票進來時，待確認會出現「待沖抵預付」` },
      ]} />
    </div>
  );
}

// ================================================================ 折讓
type Original = Doc & { allowance_room: number; tax_id: string | null };

export function AllowanceForm({ company, docDate, setDocDate }: { company: string; docDate: string; setDocDate: (d: string) => void }) {
  const toast = useToast();
  const { categories, categoryName, storeName } = useDirectory();
  const [search, setSearch] = useState("");
  const [vendor, setVendor] = useState<VendorHit | null>(null);
  const [cands, setCands] = useState<Original[]>([]);
  const [orig, setOrig] = useState<Original | null>(null);
  const [noOrig, setNoOrig] = useState(false);
  const [category, setCategory] = useState("");
  const [reason, setReason] = useState<"return" | "quality" | "shortage" | "price">("quality");
  const [allowanceNo, setAllowanceNo] = useState("");
  const [untaxed, setUntaxed] = useState("");
  const [tax, setTax] = useState<string | null>(null);
  const [period, setPeriod] = useState<"original" | "current">("original");
  const [refund, setRefund] = useState<"reduce_outstanding" | "vendor_refund" | "credit_next">("reduce_outstanding");
  const [memo, setMemo] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const net = toInt(untaxed);
  const autoTax = (orig ? orig.has_invoice : true) ? Math.round(net * 0.05) : 0;
  const taxN = tax === null ? autoTax : toInt(tax);
  const total = net + taxN;
  const paidOff = !!orig && (orig.outstanding || 0) <= 0;

  // 原發票號碼 → 直接帶出那一張
  useEffect(() => {
    const no = search.trim().toUpperCase().replace("-", "");
    if (!INVOICE_RE.test(no)) return;
    let alive = true;
    apiGet<{ items: Original[] }>("/documents/lookup", { invoice_no: no }).then((r) => {
      if (!alive) return;
      setCands(r.items);
      if (r.items.length === 1) setOrig(r.items[0]);
    }).catch(() => {});
    return () => { alive = false; };
  }, [search]);
  // 廠商 → 列出近 3 個月的應付
  useEffect(() => {
    if (!vendor) return;
    apiGet<{ items: Original[] }>("/documents/original-candidates", { counterparty_id: vendor.id, company_id: company, months: 3 })
      .then((r) => setCands(r.items)).catch(() => {});
  }, [vendor, company]);
  useEffect(() => { if (paidOff && refund === "reduce_outstanding") setRefund("vendor_refund"); }, [paidOff, refund]);

  async function save() {
    setSaving(true); setErr(null);
    try {
      const d = await apiSend<Doc>("POST", "/allowances", {
        original_document_id: noOrig ? null : orig?.id, company_id: company,
        counterparty_id: noOrig ? vendor?.id : undefined, category_code: noOrig ? category : undefined,
        doc_date: docDate, reason, untaxed: net, tax: taxN, refund_method: refund, period,
        allowance_no: allowanceNo || null, memo: memo || null });
      toast(`已建立折讓 ${money(total)}${orig ? `，原單尚欠 ${money((orig.outstanding || 0) - (refund === "reduce_outstanding" ? total : 0), { zeroDash: false })}` : ""}`);
      setOrig(null); setCands([]); setUntaxed(""); setTax(null); setAllowanceNo(""); setMemo(""); setSearch(""); setVendor(null);
      void d;
    } catch (e) { setErr(e as ApiError); } finally { setSaving(false); }
  }

  const origLine = orig?.lines?.find((l) => !l.category_code.startsWith("B")) || orig?.lines?.[0];
  return (
    <div className="row">
      <form className="card grow pad stack" style={{ gap: 18 }} aria-label="折讓單" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <section className="stack" aria-labelledby="al-1">
          <h2 id="al-1" style={{ fontSize: 15 }}>1　折讓的是哪一張應付？</h2>
          <div className="form-grid">
            <label className="f"><span>折讓單上的原發票號碼</span>
              <input className="num" value={search} onChange={(e) => setSearch(e.target.value.toUpperCase())} placeholder="例：AB12345678" />
              <span className="small">輸入完整號碼會直接帶出那一張</span></label>
            <VendorPicker label="或用廠商統編／名稱找" value={vendor} onChange={(v) => { setVendor(v); setOrig(null); }} companyId={company} required />
          </div>
          {!noOrig && cands.length > 0 && (
            <div className="tbl-wrap">
              <table>
                <thead><tr><th><span className="sr">選擇</span></th><th>發票日</th><th>發票號碼</th><th>門市・類別</th>
                  <th className="r">發票金額</th><th className="r">尚未付</th><th className="r">可折讓</th></tr></thead>
                <tbody>
                  {cands.map((c) => {
                    const l = c.lines?.find((x) => !x.category_code.startsWith("B")) || c.lines?.[0];
                    return (
                      <tr key={c.id} style={orig?.id === c.id ? { background: "var(--accent-soft)" } : undefined}>
                        <td><input type="radio" name="orig" aria-label={`選擇 ${c.invoice_nos.join("、") || c.doc_date}`}
                                   checked={orig?.id === c.id} onChange={() => setOrig(c)} /></td>
                        <td className="num">{c.doc_date}</td><td className="num">{c.invoice_nos.join("、") || "—"}</td>
                        <td>{l ? `${storeName(l.store_id)}・${l.category_code} ${categoryName(l.category_code)}` : "—"}</td>
                        <td className="num r">{money(c.settle_amount)}</td><td className="num r">{money(c.outstanding, { zeroDash: false })}</td>
                        <td className="num r">{money(c.allowance_room, { zeroDash: false })}</td>
                      </tr>);
                  })}
                </tbody>
              </table>
            </div>
          )}
          <label style={{ display: "flex", gap: 8, alignItems: "center", minHeight: 40 }}>
            <input type="checkbox" checked={noOrig} onChange={(e) => { setNoOrig(e.target.checked); setOrig(null); if (e.target.checked) setRefund("credit_next"); }} />
            沒有對應的原單（例：年度進貨回饋），直接選類別記負數</label>
          {noOrig && (
            <label className="f" style={{ maxWidth: 320 }}><span>類別</span>
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                <option value="">選擇類別</option>
                {categories.filter((c) => c.is_active && c.nature === "expense").map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}</option>)}
              </select></label>
          )}
        </section>

        <section className="stack" aria-labelledby="al-2">
          <h2 id="al-2" style={{ fontSize: 15 }}>2　折讓內容</h2>
          <Seg label="原因" value={reason} onChange={setReason}
               options={[["return", "退貨"], ["quality", "品質不符折價"], ["shortage", "數量短少"], ["price", "價格折扣・回饋"]]} />
          <div className="form-grid">
            <label className="f"><span>折讓單號（選填）</span><input className="num" value={allowanceNo} onChange={(e) => setAllowanceNo(e.target.value)} /></label>
            <label className="f"><span>折讓日期</span><input type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></label>
            <label className="f"><span>未稅金額</span><input className="amt" inputMode="numeric" value={untaxed} onChange={(e) => setUntaxed(e.target.value)} /></label>
            <label className="f"><span>稅額</span><input className="amt" inputMode="numeric" value={tax === null ? String(autoTax) : tax} onChange={(e) => setTax(e.target.value)} />
              <span className="small">{tax === null ? "依 5% 自動計算" : <button type="button" className="linkbtn small" onClick={() => setTax(null)}>改回自動計算</button>}</span></label>
            <label className="f"><span>說明</span><input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="例：牛腱品質不符" /></label>
          </div>
          <div className="small" style={{ textAlign: "right" }}>折讓合計（含稅）<span className="num" style={{ fontWeight: 600 }}> {money(-total, { zeroDash: false })}</span></div>
          {!noOrig && (
            <Seg label="成本減少記在哪個月" value={period} onChange={setPeriod}
                 options={[["original", `原發票月份${origLine ? `：${origLine.period_key}` : ""}`], ["current", `折讓當月：${docDate.slice(0, 7)}`]]} />
          )}
          <Seg label="錢怎麼回來" value={refund} onChange={setRefund}
               options={[...(!noOrig && !paidOff ? [["reduce_outstanding", "尾款少付"] as ["reduce_outstanding", string]] : []),
                         ["vendor_refund", "廠商退錢到帳戶"], ["credit_next", "留著下次進貨抵"]]} />
          {paidOff && <div className="small">原單已付清：折讓金額會變成「廠商欠我們」，等廠商退款或下次進貨抵。</div>}
        </section>

        <FieldErrors error={err} />
        <div style={{ display: "flex", gap: 10, borderTop: "1px solid var(--line)", paddingTop: 16 }}>
          <button type="submit" className="btn pri" disabled={saving || !net || (!orig && !noOrig) || (noOrig && (!vendor || !category))}>建立折讓單</button>
        </div>
      </form>
      <Effect title={orig ? `原單 ${orig.invoice_nos[0] || orig.doc_date} 調整後` : "建立後會發生什麼"} rows={orig ? [
        { k: "發票金額", v: <span className="num">{money(orig.settle_amount)}</span> },
        { k: "已付・已沖抵", v: <span className="num">{money(-((orig.settled_amount || 0) + (orig.offset_amount || 0)), { zeroDash: false })}</span> },
        { k: "本次折讓", v: <span className="num">{money(-total, { zeroDash: false })}</span> },
        { k: "尚應付", v: <b className="num">{money((orig.outstanding || 0) - (refund === "reduce_outstanding" ? total : 0), { zeroDash: false })}</b>,
          note: refund === "reduce_outstanding" ? "月底照這個金額匯款，銀行核對會判定相符" : "廠商要退的錢列在應收方向，等退款或下次進貨抵" },
        { k: "損益", v: origLine ? `${period === "original" ? origLine.period_key : docDate.slice(0, 7)} ${origLine.category_code} ${categoryName(origLine.category_code)} 減少 ${money(net)}` : "—" },
      ] : [
        { k: "損益", v: "折讓的未稅金額以負數記在原單的類別，成本跟著減少" },
        { k: "進項稅額", v: "稅額記為負數；營業稅申報時要附折讓證明單" },
      ]} />
    </div>
  );
}

// ================================================================ 代付
export function OnBehalfForm({ company, docDate, setDocDate, onUseGeneral }: {
  company: string; docDate: string; setDocDate: (d: string) => void; onUseGeneral: (presetCategory?: string) => void;
}) {
  const toast = useToast();
  const { dir, categories, companyName } = useDirectory();
  const [who, setWho] = useState<"intercompany" | "person" | "external">("intercompany");
  const [bene, setBene] = useState(company);
  const stores = useStores(bene);
  const [store, setStore] = useState("");
  const [vendor, setVendor] = useState<VendorHit | null>(null);
  const [category, setCategory] = useState("");
  const [invoiceNo, setInvoiceNo] = useState("");
  const [buyerTax, setBuyerTax] = useState("");
  const [untaxed, setUntaxed] = useState("");
  const [tax, setTax] = useState<string | null>(null);
  const [payer, setPayer] = useState("");
  const [method, setMethod] = useState<"bank_paid" | "petty_cash" | "pending">("bank_paid");
  const [memo, setMemo] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => setBene(company), [company]);
  useEffect(() => { if (vendor?.default_category_code && !category) setCategory(vendor.default_category_code); }, [vendor, category]);

  const root = (id: string) => dir.companies.find((c) => c.id === id)?.parent_company_id || id;
  const payers = dir.companies.filter((c) => bene && root(c.id) !== root(bene));
  const net = toInt(untaxed);
  const autoTax = invoiceNo.trim() || buyerTax.length === 8 ? Math.round(net * 0.05) : 0;
  const taxN = tax === null ? autoTax : toInt(tax);
  const total = net + taxN;
  const payerCo = dir.companies.find((c) => c.id === payer);
  const beneCo = dir.companies.find((c) => c.id === bene);
  const headerIsPayer = !!buyerTax && buyerTax === payerCo?.tax_id;
  const headerOdd = !!buyerTax && buyerTax.length === 8 && !headerIsPayer && buyerTax !== beneCo?.tax_id;
  const cats = useMemo(() => categories.filter((c) => c.is_active && c.nature === "expense"), [categories]);

  async function save() {
    if (!vendor) return;
    setSaving(true); setErr(null);
    try {
      const r = await apiSend<{ warnings: string[] }>("POST", "/on-behalf", {
        beneficiary_company_id: bene, store_id: store || null, vendor_id: vendor.id, category_code: category,
        payer_company_id: payer, doc_date: docDate, untaxed: net, tax: taxN, invoice_no: invoiceNo || null,
        invoice_buyer_tax_id: buyerTax || null, has_invoice: !!invoiceNo, settlement_method: method, memo: memo || null });
      toast(`已建立代付，兩家公司的往來已同步${r.warnings.length ? "（發票抬頭不符，已送待確認）" : ""}`);
      setVendor(null); setUntaxed(""); setTax(null); setInvoiceNo(""); setBuyerTax(""); setMemo("");
    } catch (e) { setErr(e as ApiError); } finally { setSaving(false); }
  }

  return (
    <div className="row">
      <form className="card grow pad stack" style={{ gap: 18 }} aria-label="代付單" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <Seg label="錢是誰先付的？" value={who} onChange={setWho}
             options={[["intercompany", "集團內其他公司代付"], ["person", "員工或股東個人先墊"], ["external", "我們幫集團外的人付"]]} />
        {who === "person" && (
          <div className="ai stack" style={{ gap: 8 }}>
            <div>費用一樣記在用的那家店：用「一般支出」，<b>對象選那位員工或股東</b>，付款方式選「待付款」，報帳匯款後由銀行核對結清。
              股東先墊、不打算近期還的，類別改選 B04 股東往來。</div>
            <div><button type="button" className="btn sm pri" onClick={() => onUseGeneral()}>改用一般支出</button></div>
          </div>
        )}
        {who === "external" && (
          <div className="ai stack" style={{ gap: 8 }}>
            <div>不進我們的損益：用「一般支出」，類別選 <b>B08 代墊款</b>；對方還錢時，那筆銀行入帳在待確認選 B08 沖回。</div>
            <div><button type="button" className="btn sm pri" onClick={() => onUseGeneral("B08")}>改用一般支出（B08）</button></div>
          </div>
        )}
        {who === "intercompany" && (<>
          <section className="stack" aria-labelledby="ob-1">
            <h2 id="ob-1" style={{ fontSize: 15 }}>費用是誰的 <span className="small">記在這家公司、這家店的損益</span></h2>
            <div className="form-grid">
              <label className="f"><span>公司</span>
                <select value={bene} onChange={(e) => { setBene(e.target.value); setStore(""); setPayer(""); }}>
                  {dir.companies.map((c) => <option key={c.id} value={c.id}>{c.parent_company_id ? "　└ " : ""}{c.name}</option>)}</select></label>
              <label className="f"><span>門市</span>
                <select value={store} onChange={(e) => setStore(e.target.value)}>
                  <option value="">公司層級</option>{stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
              <VendorPicker value={vendor} onChange={setVendor} required />
              <label className="f"><span>類別</span>
                <select value={category} onChange={(e) => setCategory(e.target.value)}>
                  <option value="">選擇類別</option>{cats.map((c) => <option key={c.code} value={c.code}>{c.code} {c.name}</option>)}</select></label>
              <label className="f"><span>發票號碼</span><input className="num" value={invoiceNo} onChange={(e) => setInvoiceNo(e.target.value.toUpperCase())} placeholder="無發票請留空" /></label>
              <label className="f"><span>發票抬頭統編</span>
                <input className="num" inputMode="numeric" value={buyerTax} onChange={(e) => setBuyerTax(e.target.value.replace(/\D/g, "").slice(0, 8))} />
                <span className="small" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {payerCo?.tax_id && <button type="button" className="linkbtn small" onClick={() => setBuyerTax(payerCo.tax_id!)}>抬頭是付款公司</button>}
                  {beneCo?.tax_id && <button type="button" className="linkbtn small" onClick={() => setBuyerTax(beneCo.tax_id!)}>抬頭是費用公司</button>}
                </span></label>
              <label className="f"><span>未稅金額</span><input className="amt" inputMode="numeric" value={untaxed} onChange={(e) => setUntaxed(e.target.value)} /></label>
              <label className="f"><span>稅額</span><input className="amt" inputMode="numeric" value={tax === null ? String(autoTax) : tax} onChange={(e) => setTax(e.target.value)} /></label>
              <label className="f"><span>說明</span><input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="例：冷凍設備維修" /></label>
            </div>
            {headerIsPayer && <div className="ai small">發票抬頭是付款公司「{payerCo?.name}」：系統會在 {payerCo?.name} 記「代付（進項）」與「代支出（銷項）」兩個科目，費用照樣記在 {companyName(bene)}。</div>}
            {headerOdd && <div className="errbox" role="alert">發票抬頭既不是費用公司、也不是付款公司，存檔後會送到待確認。</div>}
          </section>
          <section className="stack" aria-labelledby="ob-2">
            <h2 id="ob-2" style={{ fontSize: 15 }}>錢是誰付的 <span className="small">這家公司的損益不變，只多一筆「對方欠我」</span></h2>
            <div className="form-grid">
              <label className="f"><span>付款公司</span>
                <select value={payer} onChange={(e) => setPayer(e.target.value)}>
                  <option value="">選擇付款公司</option>
                  {payers.map((c) => <option key={c.id} value={c.id}>{c.parent_company_id ? "　└ " : ""}{c.name}</option>)}</select>
                <span className="small">總公司與分公司是同一法人，可以直接互付，不需要代付</span></label>
              <label className="f"><span>付款日期</span><input type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></label>
            </div>
            <Seg label="付款方式" value={method} onChange={setMethod}
                 options={[["bank_paid", "銀行已付（等現金流配對）"], ["petty_cash", "零用金"], ["pending", "還沒付"]]} />
          </section>
          <FieldErrors error={err} />
          <div style={{ display: "flex", gap: 10, borderTop: "1px solid var(--line)", paddingTop: 16 }}>
            <button type="submit" className="btn pri" disabled={saving || !vendor || !category || !payer || !net}>建立代付單</button>
          </div>
        </>)}
      </form>
      {who === "intercompany" && (
        <Effect title="兩家公司各自會看到" rows={[
          { k: `${companyName(bene)}${store ? `・${stores.find((s) => s.id === store)?.name}` : ""}`,
            v: <>損益 {category || "—"} <span className="num">+{money(net, { zeroDash: false })}</span>；B05 欠 {payerCo?.name || "付款公司"} <span className="num">{money(total, { zeroDash: false })}</span></>,
            note: "應付清單不會出現（廠商已經收到錢了）" },
          { k: payerCo?.name || "付款公司", v: <>損益 <span className="num">— 不變</span>；B05 {companyName(bene)} 欠我們 <span className="num">{money(total, { zeroDash: false })}</span></>,
            note: "這筆銀行支出會對到付款公司的代付單，不會變成「有付款沒發票」" },
          { k: "集團內往來對照表", v: <Tag kind="ok">兩邊同時出現，差額 0</Tag>, note: "還錢時兩邊一起沖掉" },
        ]} />
      )}
    </div>
  );
}
