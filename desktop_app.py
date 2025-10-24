# python
# Desktop GUI for Brain Trials Finder (no Streamlit)
# Run with: python desktop_app.py
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import urllib.parse
import webbrowser
from typing import List, Dict, Any

from ctgov_client import (
    DEFAULT_DIAG_TERMS,
    build_terms,
    fetch_all_terms,
    score_trial,
    extract_row,
    ensure_list,
)
from uk_sources import fetch_uk_trials

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]

# Predefined NIHR UK location options for portal queries
UK_NIHR_LOCATIONS = [
    "Nottingham",
    "Liverpool",
    "Preston",
    "Brighton",
    "Cardiff",
    "Leeds",
    "Plymouth",
    "Coventry",
    "Newcastle upon Tyne",
    "Dundee",
    "Cambridge",
    "Birmingham",
    "Hull",
    "Stoke-on-Trent",
    "Romford",
    "Southampton",
    "Bristol",
    "Middlesbrough",
    "London",
    "Sheffield",
    "Edinburgh",
    "Oxford",
]


class BrainTrialsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Brain Cancer Trials Finder (Desktop)")
        self.geometry("1200x760")

        # Inputs frame (top controls)
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="x")

        # Diagnosis
        ttk.Label(frm, text="Diagnosis:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        diag_options = list(DEFAULT_DIAG_TERMS.keys()) + ["Other"]
        self.diagnosis = tk.StringVar(value="Glioblastoma")
        ttk.Combobox(frm, textvariable=self.diagnosis, values=diag_options, state="readonly", width=28).grid(row=0, column=1, sticky=tk.W)

        # Setting
        ttk.Label(frm, text="Setting:").grid(row=0, column=2, sticky=tk.W, padx=(16, 6))
        self.setting = tk.StringVar(value="Recurrent")
        ttk.Combobox(frm, textvariable=self.setting, values=["Newly diagnosed", "Recurrent"], state="readonly", width=20).grid(row=0, column=3, sticky=tk.W)

        # Age
        ttk.Label(frm, text="Age:").grid(row=0, column=4, sticky=tk.W, padx=(16, 6))
        self.age = tk.IntVar(value=55)
        tk.Spinbox(frm, from_=1, to=100, textvariable=self.age, width=6).grid(row=0, column=5, sticky=tk.W)

        # KPS
        ttk.Label(frm, text="KPS:").grid(row=0, column=6, sticky=tk.W, padx=(16, 6))
        self.kps = tk.IntVar(value=80)
        tk.Spinbox(frm, from_=40, to=100, increment=10, textvariable=self.kps, width=6).grid(row=0, column=7, sticky=tk.W)

        # Prior bev
        self.prior_bev = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Prior bevacizumab", variable=self.prior_bev).grid(row=1, column=1, sticky=tk.W, pady=(6, 0))

        # Keywords
        ttk.Label(frm, text="Keywords:").grid(row=1, column=2, sticky=tk.W, padx=(16, 6), pady=(6, 0))
        self.keywords = tk.StringVar(value="immunotherapy,vaccine,device")
        ttk.Entry(frm, textvariable=self.keywords, width=32).grid(row=1, column=3, sticky=tk.W, pady=(6, 0))

        # Country filter (optional)
        ttk.Label(frm, text="Country contains:").grid(row=1, column=4, sticky=tk.W, padx=(16, 6), pady=(6, 0))
        self.country = tk.StringVar(value="")
        ttk.Entry(frm, textvariable=self.country, width=18).grid(row=1, column=5, sticky=tk.W, pady=(6, 0))
        self.require_country = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Require site in country", variable=self.require_country).grid(row=1, column=6, sticky=tk.W, pady=(6, 0))

        # Buttons
        self.btn_search = ttk.Button(frm, text="Search", command=self.on_search)
        self.btn_search.grid(row=0, column=8, sticky=tk.W, padx=(16, 0))
        self.status_lbl = ttk.Label(frm, text="Ready")
        self.status_lbl.grid(row=1, column=8, sticky=tk.W, padx=(16, 0))

        # UK Sources section
        ukfrm = ttk.Labelframe(self, text="UK Sources", padding=10)
        ukfrm.pack(fill="x", padx=10)
        self.uk_use_ctgov = tk.BooleanVar(value=True)
        ttk.Checkbutton(ukfrm, text="ClinicalTrials.gov (UK sites only)", variable=self.uk_use_ctgov).grid(row=0, column=0, sticky=tk.W)
        self.btn_search_uk = ttk.Button(ukfrm, text="Search UK", command=self.on_search_uk)
        self.btn_search_uk.grid(row=0, column=1, padx=(16, 0))
        ttk.Button(ukfrm, text="Save PDF", command=self.on_save_pdf).grid(row=0, column=2, padx=(16, 0))
        # Open portal shortcuts
        ttk.Button(ukfrm, text="Open NIHR", command=self.on_open_nihr).grid(row=1, column=0, pady=(8, 0), sticky=tk.W)
        ttk.Button(ukfrm, text="Open ISRCTN (UK)", command=self.on_open_isrctn).grid(row=1, column=1, pady=(8, 0), sticky=tk.W)
        ttk.Button(ukfrm, text="Open CRUK", command=self.on_open_cruk).grid(row=1, column=2, pady=(8, 0), sticky=tk.W)
        # NIHR specific location (optional)
        ttk.Label(ukfrm, text="NIHR location (optional):").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        self.uk_location = tk.StringVar(value="")
        ttk.Combobox(ukfrm, textvariable=self.uk_location, values=UK_NIHR_LOCATIONS, width=28, state="readonly").grid(row=2, column=1, sticky=tk.W, pady=(8, 0))

        # Results tree
        cols = ("score", "title", "sponsor", "city_country", "status", "phases", "conditions", "nct")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(6, 10))
        self.tree.heading("score", text="Score")
        self.tree.heading("title", text="Title")
        self.tree.heading("sponsor", text="Sponsor")
        self.tree.heading("city_country", text="City/Country")
        self.tree.heading("status", text="Status")
        self.tree.heading("phases", text="Phases")
        self.tree.heading("conditions", text="Conditions")
        self.tree.heading("nct", text="NCT ID")
        self.tree.column("score", width=60, anchor="center")
        self.tree.column("title", width=330)
        self.tree.column("sponsor", width=220)
        self.tree.column("city_country", width=160)
        self.tree.column("status", width=120)
        self.tree.column("phases", width=110)
        self.tree.column("conditions", width=260)
        self.tree.column("nct", width=120)
        self.tree.bind("<Double-1>", self.on_open)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # Store per-row mappings
        self._url_by_item: Dict[str, str] = {}
        self._study_by_item: Dict[str, Dict[str, Any]] = {}
        self._current_rows: List[Dict[str, Any]] = []  # rows currently displayed

        # Contacts and Locations panel
        infofrm = ttk.Labelframe(self, text="Contacts and Locations", padding=10)
        infofrm.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.contacts_text = tk.Text(infofrm, height=12, wrap="word")
        self.contacts_text.config(state="disabled")
        scroll = ttk.Scrollbar(infofrm, orient="vertical", command=self.contacts_text.yview)
        self.contacts_text.configure(yscrollcommand=scroll.set)
        self.contacts_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        infofrm.columnconfigure(0, weight=1)
        infofrm.rowconfigure(0, weight=1)

        # Initial load (use lambda to satisfy type checkers)
        self.after(100, lambda: self.on_search())

    # ----- Portal helpers -----
    def _build_portal_query(self) -> str:
        diag = (self.diagnosis.get() or "").strip()
        if diag and diag != "Other":
            q = diag
        else:
            q = (self.keywords.get() or "").strip() or "brain tumour"
        return urllib.parse.quote_plus(q)

    def on_open_nihr(self):
        q = self._build_portal_query()
        base = "https://www.bepartofresearch.nihr.ac.uk/results/search-results"
        loc_txt = (self.uk_location.get() or "").strip()
        if loc_txt:
            loc = urllib.parse.quote_plus(loc_txt)
            url = f"{base}?query={q}&location={loc}"
        else:
            url = f"{base}?query={q}"
        webbrowser.open_new_tab(url)

    def on_open_isrctn(self):
        q = self._build_portal_query()
        url = f"https://www.isrctn.com/search?q={q}&countries=United%20Kingdom"
        webbrowser.open_new_tab(url)

    def on_open_cruk(self):
        q = self._build_portal_query()
        url = f"https://find.cancerresearchuk.org/clinical-trials?q={q}"
        webbrowser.open_new_tab(url)

    # ----- Actions -----
    def on_open(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        for iid in sel:
            url = self._url_by_item.get(iid)
            if url:
                webbrowser.open_new_tab(url)
                break

    def on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        study = self._study_by_item.get(iid)
        if study:
            self._populate_contacts(study)

    def on_search(self):
        self.btn_search.configure(state=tk.DISABLED)
        self.btn_search_uk.configure(state=tk.DISABLED)
        self.status_lbl.configure(text="Fetching…")
        diagnosis = self.diagnosis.get()
        setting = self.setting.get()
        age = self.age.get()
        kps = self.kps.get()
        prior_bev = self.prior_bev.get()
        keywords = self.keywords.get()
        country = self.country.get().strip()
        require_country = self.require_country.get()

        def worker():
            try:
                terms = build_terms(diagnosis, keywords)
                studies = fetch_all_terms(terms, STATUSES, page_size=100, max_pages=5)
                rows: List[Dict[str, Any]] = []
                skipped = 0
                for s in studies:
                    try:
                        ps = (s.get("protocolSection", {}) or {})
                        clm = (ps.get("contactsLocationsModule", {}) or {})
                        locs = ensure_list(clm.get("locations"))
                        if country and require_country:
                            locs = [L for L in locs if country.lower() in (L.get("locationCountry") or "").lower()]
                        if require_country and not locs:
                            continue
                        intake = {
                            "age": age,
                            "kps": kps,
                            "prior_bev": prior_bev,
                            "setting": setting,
                            "keywords": keywords,
                            "diagnosis": diagnosis,
                        }
                        sc, reasons = score_trial(s, intake)
                        base = extract_row(s)
                        # Ensure city_country exists (fallback from first location)
                        if not base.get("city_country"):
                            first = locs[0] if locs else None
                            if first:
                                city = (first.get("locationCity") or "").strip()
                                country1 = (first.get("locationCountry") or "").strip()
                                parts = [p for p in [city, country1] if p]
                                if parts:
                                    base["city_country"] = ", ".join(parts)

                        base["score"] = sc
                        base["reasons"] = "; ".join(reasons)
                        base["url"] = f"https://clinicaltrials.gov/study/{base['nct']}" if base.get("nct") else ""
                        base["study"] = s
                        rows.append(base)
                    except Exception:
                        skipped += 1
                        continue
                rows.sort(key=lambda x: -x.get("score", 0))
                self.after(0, self._render_rows, rows, skipped, len(studies))
            except Exception as e:
                self.after(0, self._show_error, e)

        threading.Thread(target=worker, daemon=True).start()

    def on_search_uk(self):
        self.btn_search.configure(state=tk.DISABLED)
        self.btn_search_uk.configure(state=tk.DISABLED)
        self.status_lbl.configure(text="Fetching UK trials…")
        diagnosis = self.diagnosis.get()
        setting = self.setting.get()
        age = self.age.get()
        kps = self.kps.get()
        prior_bev = self.prior_bev.get()
        keywords = self.keywords.get()
        use_ctgov = self.uk_use_ctgov.get()

        def worker():
            try:
                intake = {
                    "age": age,
                    "kps": kps,
                    "prior_bev": prior_bev,
                    "setting": setting,
                    "keywords": keywords,
                    "diagnosis": diagnosis,
                }
                rows, total_raw, skipped = fetch_uk_trials(diagnosis, keywords, intake, include_ctgov=use_ctgov)
                self.after(0, self._render_rows, rows, skipped, total_raw)
            except Exception as e:
                self.after(0, self._show_error, e)

        threading.Thread(target=worker, daemon=True).start()

    # ----- Rendering & details -----
    def _show_error(self, e: Exception):
        self.btn_search.configure(state=tk.NORMAL)
        self.btn_search_uk.configure(state=tk.NORMAL)
        self.status_lbl.configure(text="Error")
        messagebox.showerror("Error", f"Failed to fetch trials.\n{e}")

    def _render_rows(self, rows: List[Dict[str, Any]], skipped: int, total: int):
        # Clear
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._url_by_item.clear()
        self._study_by_item.clear()
        self._current_rows = rows[:]  # snapshot for export

        # Insert
        for r in rows[:300]:
            values = (
                r.get("score", 0),
                r.get("title", ""),
                r.get("sponsor", ""),
                r.get("city_country", ""),
                r.get("status", ""),
                r.get("phases", ""),
                r.get("conditions", ""),
                r.get("nct", ""),
            )
            iid = self.tree.insert("", "end", values=values)
            if r.get("url"):
                self._url_by_item[iid] = r["url"]
            if r.get("study"):
                self._study_by_item[iid] = r["study"]

        txt = f"Fetched {total} trials; showing {len(rows)} after filters."
        if skipped:
            txt += f" Skipped {skipped}."
        self.status_lbl.configure(text=txt)
        self.btn_search.configure(state=tk.NORMAL)
        self.btn_search_uk.configure(state=tk.NORMAL)

    def _populate_contacts(self, study: Dict[str, Any]):
        ps = (study.get("protocolSection", {}) or {})
        clm = (ps.get("contactsLocationsModule", {}) or {})
        lines: List[str] = []

        # Central contacts
        centrals = ensure_list(clm.get("centralContacts"))
        if centrals:
            lines.append("Central Contacts:")
            for c in centrals:
                name = (c.get("name") or "").strip()
                role = (c.get("role") or "").strip()
                phone = (c.get("phone") or "").strip()
                email = (c.get("email") or "").strip()
                parts = [p for p in [name, role, phone, email] if p]
                if parts:
                    lines.append("  - " + " | ".join(parts))

        # Overall officials
        officials = ensure_list(clm.get("overallOfficials"))
        if officials:
            lines.append("Overall Officials:")
            for o in officials:
                name = (o.get("name") or "").strip()
                role = (o.get("role") or "").strip()
                aff = (o.get("affiliation") or "").strip()
                parts = [p for p in [name, role, aff] if p]
                if parts:
                    lines.append("  - " + " | ".join(parts))

        # Locations
        locs = ensure_list(clm.get("locations"))
        if locs:
            lines.append("Locations:")
            for L in locs:
                facility = (L.get("locationFacility") or "").strip()
                city = (L.get("locationCity") or "").strip()
                state = (L.get("locationState") or "").strip()
                country = (L.get("locationCountry") or "").strip()
                status = (L.get("status") or "").strip()
                site_line = ", ".join([p for p in [facility, city, state, country] if p])
                if site_line:
                    if status:
                        lines.append(f"  - {site_line} (status: {status})")
                    else:
                        lines.append(f"  - {site_line}")
                # per-location contacts
                lcontacts = ensure_list(L.get("contacts")) or ensure_list(L.get("locationContacts"))
                for lc in lcontacts:
                    lname = (lc.get("name") or "").strip()
                    lrole = (lc.get("role") or "").strip()
                    lphone = (lc.get("phone") or "").strip()
                    lemail = (lc.get("email") or "").strip()
                    parts = [p for p in [lname, lrole, lphone, lemail] if p]
                    if parts:
                        lines.append("      • " + " | ".join(parts))

        if not lines:
            lines.append("No contacts/locations provided by sponsor at this time.")

        self.contacts_text.config(state="normal")
        self.contacts_text.delete("1.0", tk.END)
        self.contacts_text.insert(tk.END, "\n".join(lines))
        self.contacts_text.config(state="disabled")

    # ----- PDF export -----
    def on_save_pdf(self):
        if not self._current_rows:
            messagebox.showinfo("Save PDF", "No results to export. Perform a search first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save PDF",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile="brain_trials_results.pdf",
        )
        if not path:
            return
        try:
            self._export_pdf(self._current_rows, path)
            messagebox.showinfo("Save PDF", f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Save PDF", f"Failed to create PDF.\n{e}")

    def _export_pdf(self, rows: List[Dict[str, Any]], path: str):
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.units import mm

        doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph("Brain Cancer Trials – Results", styles["Title"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"Total shown: {len(rows)}", styles["Normal"]))
        story.append(Spacer(1, 12))

        for r in rows:
            title = r.get("title", "")
            nct = r.get("nct", "")
            sponsor = r.get("sponsor", "")
            status = r.get("status", "")
            phases = r.get("phases", "")
            city_country = r.get("city_country", "")
            score = r.get("score", 0)
            url = f"https://clinicaltrials.gov/study/{nct}" if nct else ""
            story.append(Paragraph(f"<b>{title}</b>", styles["Heading4"]))
            meta = (
                f"NCT: {nct or '—'} | Sponsor: {sponsor or '—'} | City/Country: {city_country or '—'} | "
                f"Status: {status or '—'} | Phases: {phases or '—'} | Score: {score}"
            )
            story.append(Paragraph(meta, styles["Normal"]))
            if url:
                story.append(Paragraph(f"URL: <a href='{url}' color='blue'>{url}</a>", styles["Normal"]))
            story.append(Spacer(1, 8))

        doc.build(story)


if __name__ == "__main__":
    app = BrainTrialsApp()
    app.mainloop()
