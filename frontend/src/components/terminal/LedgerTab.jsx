import { useEffect, useState } from "react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../../lib/api";

const field = "w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-1.5 text-white text-sm";

export default function LedgerTab({ user }) {
  const [wallets, setWallets] = useState([]);
  const [journals, setJournals] = useState([]);
  const [profile, setProfile] = useState(null);
  const [kyc, setKyc] = useState({ legalName: "", dateOfBirth: "" });
  const admin = user?.role === "admin";
  const load = async () => {
    try {
      const [w, j, p] = await Promise.all([api.get("/wallets"), api.get("/ledger/journals?pageSize=15"), api.get("/profile")]);
      setWallets(w.data.wallets); setJournals(j.data.items); setProfile(p.data);
    } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  useEffect(() => { load(); }, []);
  const saveProfile = async () => {
    try { await api.patch("/profile", { firstName: profile.profile.firstName, lastName: profile.profile.lastName, timezone: profile.profile.timezone }); toast.success("Profile saved"); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  const submitKyc = async () => {
    try { await api.post("/kyc/submit", kyc); toast.success("KYC submitted · in review"); load(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  const reverse = async (id) => {
    const reason = window.prompt("Reason for reversal:"); if (!reason) return;
    try { await api.post("/ledger/reversals", { journalId: id, reason }); toast.success("Reversal posted"); load(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  const setP = (k, v) => setProfile({ ...profile, profile: { ...profile.profile, [k]: v } });
  return (
    <div className="space-y-4" data-testid="ledger-tab">
      <div>
        <div className="panel-label text-[9px] mb-2">LEDGER WALLETS · DOUBLE-ENTRY BALANCES</div>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2" data-testid="ledger-wallets">
          {wallets.map((w) => (
            <div key={w.currency} className="border border-term-border p-2 text-center">
              <div className="text-[9px] text-term-muted">{w.currency}</div>
              <div className="tnum text-sm text-white">{w.available}</div>
              {w.pending > 0 && <div className="text-[9px] text-neon-amber">pending {w.pending}</div>}
            </div>
          ))}
        </div>
        <p className="text-[9px] text-term-muted mt-1">Mirrors deposits, withdrawals, realised trade P&L and exchange fees as balanced journals. Posted journals are immutable; corrections are reversal journals.</p>
      </div>
      <div>
        <div className="panel-label text-[9px] mb-2">RECENT JOURNALS</div>
        <div className="space-y-1 max-h-48 overflow-y-auto text-[10px]" data-testid="ledger-journals">
          {journals.length === 0 && <div className="text-term-muted">No journals yet.</div>}
          {journals.map((j) => (
            <div key={j.id} className="border border-term-border p-2">
              <div className="flex justify-between gap-2">
                <span className="text-white truncate">{j.description}</span>
                <span className={j.status === "POSTED" ? "text-neon-green" : j.status === "REVERSED" ? "text-neon-amber" : "text-term-muted"}>{j.status}</span>
              </div>
              <div className="text-term-muted">{j.reference_type} · {new Date(j.created_at).toLocaleString()}</div>
              {j.lines.map((l) => <div key={l.id} className="tnum text-term-muted">{l.debit !== "0" ? "DR" : "CR"} {l.account_code.replace(`USER_${user?.id}_`, "USER_")} {l.debit !== "0" ? l.debit : l.credit} {l.currency}</div>)}
              {admin && j.status === "POSTED" && <button data-testid={`reverse-${j.id}`} onClick={() => reverse(j.id)} className="mt-1 text-neon-red underline">reverse</button>}
            </div>
          ))}
        </div>
      </div>
      {profile && (
        <div className="border-t border-term-border pt-3 space-y-2">
          <div className="panel-label text-[9px]">PROFILE · KYC {profile.kyc.status}</div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <input data-testid="profile-first-name" placeholder="first name" value={profile.profile.firstName || ""} onChange={(e) => setP("firstName", e.target.value)} className={field} />
            <input data-testid="profile-last-name" placeholder="last name" value={profile.profile.lastName || ""} onChange={(e) => setP("lastName", e.target.value)} className={field} />
            <input data-testid="profile-timezone" placeholder="timezone" value={profile.profile.timezone || ""} onChange={(e) => setP("timezone", e.target.value)} className={field} />
          </div>
          <button data-testid="profile-save" onClick={saveProfile} className="w-full border border-neon-cyan text-neon-cyan hover:bg-neon-cyan hover:text-black py-1.5 text-[10px] tracking-[0.12em]">SAVE PROFILE</button>
          {profile.kyc.status !== "APPROVED" && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <input data-testid="kyc-legal-name" placeholder="legal name" value={kyc.legalName} onChange={(e) => setKyc({ ...kyc, legalName: e.target.value })} className={field} />
              <input data-testid="kyc-dob" type="date" value={kyc.dateOfBirth} onChange={(e) => setKyc({ ...kyc, dateOfBirth: e.target.value })} className={field} />
              <button data-testid="kyc-submit" onClick={submitKyc} disabled={!kyc.legalName || !kyc.dateOfBirth} className="border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black py-1.5 text-[10px] tracking-[0.12em] disabled:opacity-30">SUBMIT KYC</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
